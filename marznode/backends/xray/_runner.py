"""run xray and capture the logs"""

import asyncio
import atexit
import logging
import re
import time
from collections import deque, defaultdict

from anyio import create_memory_object_stream, ClosedResourceError, BrokenResourceError
from anyio.streams.memory import MemoryObjectReceiveStream

from ._config import XrayConfig
from ._utils import get_version

logger = logging.getLogger(__name__)

# Регулярное выражение для парсинга access логов Xray
# Формат: "from IP:PORT ... email: UID.username" или "from tcp:IP:PORT ... email: UID.username"
# Сначала идет IP, потом email (порядок важен!)
ACCESS_LOG_RE = re.compile(
    r"from\s+(?:tcp:|udp:)?(?P<ip>[0-9a-fA-F:.]+):\d+\s+.*?\s+email:\s+(?P<email>[\w.\-@]+)",
    re.IGNORECASE
)


class XrayCore:
    """runs and captures xray logs"""
    
    # Настройки для оптимизации работы с большими объемами данных
    MAX_META_ENTRIES = 10000  # Максимальное количество записей в кэше метаданных
    META_TTL = 3600  # Время жизни записи в секундах (1 час)
    CLEANUP_INTERVAL = 300  # Интервал очистки старых записей (5 минут)

    def __init__(self, executable_path: str, assets_path: str):
        self.executable_path = executable_path
        self.assets_path = assets_path

        self.version = get_version(executable_path)
        self._process = None
        self.restarting = False

        self._snd_streams = []
        self._logs_buffer = deque(maxlen=100)
        self._env = {"XRAY_LOCATION_ASSET": assets_path}
        self.stop_event = asyncio.Event()
        
        # Словарь для хранения метаданных пользователей (IP, timestamp)
        # Формат: {uid: {"remote_ip": "1.2.3.4", "timestamp": 1234567890}}
        self._last_meta: dict[int, dict] = {}
        self._last_cleanup = time.time()

        atexit.register(lambda: asyncio.run(self.stop()) if self.running else None)

    async def start(self, config: XrayConfig):
        if self.running is True:
            raise RuntimeError("Xray is started already")

        # Настраиваем логирование
        if "log" not in config:
            config["log"] = {}
        
        if config.get("log", {}).get("loglevel") in ("none", "error"):
            config["log"]["loglevel"] = "warning"
        
        # Добавляем access логи для отслеживания IP адресов
        if "access" not in config["log"]:
            # Используем stderr для access логов, чтобы мы могли их читать
            config["log"]["access"] = ""  # Пустая строка = stderr
            logger.info("Enabled access logging to stderr for IP tracking")

        cmd = [self.executable_path, "run", "-config", "stdin:"]
        self._process = await asyncio.create_subprocess_shell(
            " ".join(cmd),
            env=self._env,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        self._process.stdin.write(str.encode(config.to_json()))
        await self._process.stdin.drain()
        self._process.stdin.close()
        await self._process.stdin.wait_closed()
        logger.info("Xray core %s started", self.version)

        logs_stm = self.get_logs_stm()
        asyncio.create_task(self.__capture_process_logs())

        async def reach_startup_result(stream):
            async for line in stream:
                if line == b"" or re.match(
                    r".*\[Warning] core: Xray \d+\.\d+\.\d+ started", line.decode()
                ):  # either start or die
                    logs_stm.close()
                    return

        try:
            await asyncio.wait_for(reach_startup_result(logs_stm), timeout=4)
        except asyncio.TimeoutError:
            pass

    async def stop(self):
        """stops xray if it is started"""
        if not self.running:
            return

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=3)
        except asyncio.TimeoutError:
            logger.debug("killing xray process")
            self._process.kill()
        self._process = None

    async def restart(self, config: XrayConfig):
        """restart xray"""
        if self.restarting is True:
            return

        try:
            self.restarting = True
            logger.warning("Restarting Xray core")
            await self.stop()
            await self.start(config)
        finally:
            self.restarting = False

    def _cleanup_old_meta(self):
        """Очищает устаревшие записи метаданных для экономии памяти"""
        current_time = time.time()
        
        # Проверяем, нужна ли очистка
        if current_time - self._last_cleanup < self.CLEANUP_INTERVAL:
            return
        
        self._last_cleanup = current_time
        cutoff_time = current_time - self.META_TTL
        
        # Удаляем устаревшие записи
        expired_uids = [
            uid for uid, meta in self._last_meta.items()
            if meta.get("timestamp", 0) < cutoff_time
        ]
        
        for uid in expired_uids:
            del self._last_meta[uid]
        
        if expired_uids:
            logger.debug(f"Cleaned up {len(expired_uids)} expired metadata entries")
        
        # Если превышен лимит записей, удаляем самые старые
        if len(self._last_meta) > self.MAX_META_ENTRIES:
            # Сортируем по timestamp и удаляем самые старые
            sorted_items = sorted(
                self._last_meta.items(),
                key=lambda x: x[1].get("timestamp", 0)
            )
            to_remove = len(self._last_meta) - self.MAX_META_ENTRIES
            for uid, _ in sorted_items[:to_remove]:
                del self._last_meta[uid]
            
            logger.warning(
                f"Meta cache exceeded limit ({self.MAX_META_ENTRIES}), "
                f"removed {to_remove} oldest entries"
            )
    
    def _handle_log_line(self, line: str) -> bool:
        """Парсит строку лога для извлечения email и IP пользователя
        
        Оптимизировано для работы с большими объемами данных:
        - Быстрый парсинг с помощью регулярного выражения
        - Автоматическая очистка старых записей
        - Ограничение размера кэша метаданных
        
        Returns:
            True если строка успешно распарсена, False иначе
        """
        try:
            # Быстрая проверка - содержит ли строка ключевые слова
            if "email:" not in line or "from" not in line:
                return False
            
            match = ACCESS_LOG_RE.search(line)
            if not match:
                return False
            
            email = match.group("email")
            ip = match.group("ip")
            
            # Извлекаем uid из email (формат: "uid.username" или просто "uid")
            try:
                uid = int(email.split(".")[0])
            except (ValueError, IndexError):
                # Если не удалось извлечь uid, пропускаем
                return False
            
            # Сохраняем IP и timestamp в метаданные пользователя
            current_time = time.time()
            self._last_meta[uid] = {
                "remote_ip": ip,
                "timestamp": current_time
            }
            
            # Периодически очищаем старые записи
            self._cleanup_old_meta()
            
            logger.debug(f"Captured IP {ip} for user {uid} from access log")
            return True
            
        except Exception as e:
            # Не логируем каждую ошибку, чтобы не засорять логи
            logger.debug(f"Error parsing access log line: {e}")
            return False

    async def __capture_process_logs(self):
        """capture the logs, push it into the stream, and store it in the deck
        note that the stream blocks sending if it's full, so a deck is necessary"""
        process = self._process
        async def capture_stream(stream):
            while True:
                output = await stream.readline()
                for stm in self._snd_streams:
                    try:
                        await stm.send(output)
                    except (ClosedResourceError, BrokenResourceError):
                        try:
                            self._snd_streams.remove(stm)
                        except ValueError:
                            pass
                        continue
                
                # Парсим строку для извлечения метаданных
                if output and output != b"":
                    try:
                        line = output.decode(errors="ignore").strip()
                        self._handle_log_line(line)
                    except Exception as e:
                        logger.debug(f"Error handling log line: {e}")
                
                self._logs_buffer.append(output)
                if output == b"":
                    """break in case of eof"""
                    return

        await asyncio.gather(
            capture_stream(process.stderr), capture_stream(process.stdout)
        )

        # Завершаем процесс, если он еще существует
        try:
            if process and process.returncode is None:
                await process.communicate()
        except Exception as e:
            logger.debug(f"Error communicating with process: {e}")
        
        logger.warning("Xray stopped/died")
        self.stop_event.set()

    def get_logs_stm(self) -> MemoryObjectReceiveStream:
        new_snd_stm, new_rcv_stm = create_memory_object_stream()
        self._snd_streams.append(new_snd_stm)
        return new_rcv_stm

    def get_buffer(self):
        """makes a copy of the buffer, so it could be read multiple times
        the buffer is never cleared in case logs from xray's exit are useful"""
        return self._logs_buffer.copy()
    
    def _parse_access_log_file(self, log_path: str = "/var/log/xray/access.log", max_lines: int = 500):
        """Парсит файл access.log для извлечения IP адресов пользователей
        
        Args:
            log_path: путь к файлу access.log
            max_lines: максимальное количество строк для чтения (с конца файла)
        """
        try:
            import os
            
            logger.info(f"Attempting to read access log: {log_path}")
            
            if not os.path.exists(log_path):
                logger.warning(f"Access log file not found: {log_path}")
                # Пробуем найти файл в других местах
                alt_paths = [
                    "/var/log/xray/access.log",
                    "/var/lib/marznode/xray_access.log",
                    "/app/xray_access.log",
                ]
                for alt_path in alt_paths:
                    if os.path.exists(alt_path):
                        logger.info(f"Found alternative log file: {alt_path}")
                        log_path = alt_path
                        break
                else:
                    logger.error(f"No access log file found in any location")
                    return
            
            # Читаем последние N строк файла
            with open(log_path, 'rb') as f:
                # Получаем размер файла
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                logger.info(f"Access log file size: {file_size} bytes")
                
                if file_size == 0:
                    logger.warning("Access log file is empty")
                    return
                
                # Если файл маленький, читаем весь
                if file_size < max_lines * 200:  # ~200 bytes per line average
                    f.seek(0)
                    lines = f.readlines()
                else:
                    # Читаем последние max_lines строк
                    f.seek(max(0, file_size - max_lines * 200))
                    lines = f.readlines()[1:]  # Пропускаем первую неполную строку
                
                logger.info(f"Read {len(lines)} lines from access log")
                
                # Показываем пример строки для отладки
                if lines:
                    sample_line = lines[-1].decode('utf-8', errors='ignore').strip()
                    logger.info(f"Sample log line: {sample_line[:200]}")
                
                # Парсим последние строки
                parsed_count = 0
                for line_bytes in lines[-max_lines:]:
                    try:
                        line = line_bytes.decode('utf-8', errors='ignore').strip()
                        if self._handle_log_line(line):
                            parsed_count += 1
                    except Exception as e:
                        logger.debug(f"Error parsing log line: {e}")
                
                logger.info(f"Successfully parsed {parsed_count} log lines with user data")
                        
        except Exception as e:
            logger.error(f"Error reading access log file: {e}", exc_info=True)
    
    def get_last_meta(self) -> dict[int, dict]:
        """Возвращает последние метаданные по пользователям (IP, user_agent и т.д.)
        
        Возвращает копию словаря без служебных полей (timestamp).
        Оптимизировано для работы с большими объемами данных.
        """
        # Сначала парсим файл логов, если он есть
        self._parse_access_log_file()
        
        # Возвращаем только remote_ip, исключая timestamp
        return {
            uid: {"remote_ip": meta["remote_ip"]}
            for uid, meta in self._last_meta.items()
            if "remote_ip" in meta
        }

    @property
    def running(self):
        return self._process and self._process.returncode is None
