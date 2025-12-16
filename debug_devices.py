#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диагностический скрипт для проверки отслеживания устройств пользователей в marznode
"""

import asyncio
import sys
import json
import re
from pathlib import Path

# Настройка кодировки для Windows
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

# Цвета для вывода
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.ENDC}\n")


def print_success(text):
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_error(text):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_warning(text):
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")


def print_info(text):
    print(f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}")


def check_xray_config():
    """Проверяет конфигурацию Xray на наличие access логов"""
    print_header("1. Проверка конфигурации Xray")
    
    # Проверяем оба возможных пути (для Docker и локального запуска)
    config_paths = [
        Path("/var/lib/marznode/xray_config.json"),  # Docker volume
        Path("xray_config.json"),  # Локальный файл
        Path("/opt/marznode/xray_config.json"),  # Альтернативный путь
    ]
    
    config_path = None
    for path in config_paths:
        if path.exists():
            config_path = path
            break
    
    if not config_path:
        print_error(f"Конфигурационный файл не найден ни в одном из путей:")
        for path in config_paths:
            print(f"  - {path}")
        return False
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        print_success(f"Конфигурационный файл загружен: {config_path}")
        
        # Проверка секции log
        if 'log' not in config:
            print_error("Секция 'log' отсутствует в конфигурации")
            return False
        
        log_config = config['log']
        print_info(f"Текущая конфигурация логов: {json.dumps(log_config, indent=2)}")
        
        # Проверка loglevel
        loglevel = log_config.get('loglevel', 'warning')
        print_info(f"Уровень логирования: {loglevel}")
        
        if loglevel in ['none', 'error']:
            print_warning(
                f"Уровень логирования '{loglevel}' слишком низкий для отслеживания подключений.\n"
                f"  Рекомендуется использовать 'warning' или 'info'"
            )
        else:
            print_success(f"Уровень логирования '{loglevel}' подходит")
        
        # Проверка access логов
        if 'access' not in log_config:
            print_error(
                "❌ ПРОБЛЕМА НАЙДЕНА: Отсутствует настройка 'access' в секции 'log'!\n"
                "   Без access логов Xray НЕ записывает информацию о подключениях пользователей."
            )
            print_info("\n📝 Для исправления добавьте в конфигурацию:")
            print(f"{Colors.OKBLUE}")
            print('  "log": {')
            print('    "loglevel": "warning",')
            print('    "access": "/tmp/xray-access.log"  // или любой другой путь')
            print('  }')
            print(f"{Colors.ENDC}")
            return False
        else:
            access_log = log_config['access']
            print_success(f"Access лог настроен: {access_log}")
            
            # Проверка доступности файла
            if access_log != "":
                access_path = Path(access_log)
                if access_path.exists():
                    print_success(f"Файл access лога существует: {access_path}")
                    # Показываем последние строки
                    try:
                        with open(access_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()[-10:]
                            if lines:
                                print_info(f"\nПоследние {len(lines)} строк из access лога:")
                                for line in lines:
                                    print(f"  {line.rstrip()}")
                            else:
                                print_warning("Файл access лога пустой")
                    except Exception as e:
                        print_warning(f"Не удалось прочитать access лог: {e}")
                else:
                    print_warning(f"Файл access лога не существует: {access_path}")
            else:
                print_info("Access лог направлен в stdout/stderr")
        
        return True
        
    except json.JSONDecodeError as e:
        print_error(f"Ошибка парсинга JSON: {e}")
        return False
    except Exception as e:
        print_error(f"Ошибка при чтении конфигурации: {e}")
        return False


def check_access_log_format():
    """Проверяет формат access логов и регулярное выражение"""
    print_header("2. Проверка формата access логов")
    
    # НОВОЕ регулярное выражение из обновленного кода
    ACCESS_LOG_RE = re.compile(
        r"from\s+(?:tcp:|udp:)?(?P<ip>[0-9a-fA-F:.]+):\d+\s+.*?\s+email:\s+(?P<email>[\w.\-@]+)",
        re.IGNORECASE
    )
    
    print_info("Регулярное выражение для парсинга логов (ОБНОВЛЕНО):")
    print(f"  {ACCESS_LOG_RE.pattern}")
    
    # Примеры РЕАЛЬНЫХ форматов логов Xray (обновлено на основе фактических данных)
    test_logs = [
        "2025/12/16 13:14:39.238373 from tcp:188.243.10.156:49322 accepted udp:77.88.8.8:53 [RU Direct -> direct] email: 35.fc3678c6a43c4e449632bbdab138ce5d",
        "2025/12/16 13:14:39.307987 from 188.243.10.156:49350 accepted tcp:example.com:443 [RU Direct -> direct] email: 123.testuser",
        "from 192.168.1.1:12345 accepted tcp:8.8.8.8:53 email: 456.username",
        "from tcp:10.0.0.1:54321 accepted udp:1.1.1.1:53 email: 789.user",
    ]
    
    print_info("\nТестирование регулярного выражения на реальных примерах:")
    matches_found = False
    for log in test_logs:
        match = ACCESS_LOG_RE.search(log)
        if match:
            print_success(f"✓ Совпадение найдено:")
            print(f"    Лог: {log[:100]}...")
            print(f"    Email: {match.group('email')}, IP: {match.group('ip')}")
            matches_found = True
        else:
            print_warning(f"✗ Совпадение не найдено: {log[:80]}...")
    
    if not matches_found:
        print_error(
            "\nРегулярное выражение не совпадает ни с одним из тестовых форматов!\n"
            "Это может означать, что формат логов Xray изменился."
        )
    else:
        print_success("\n✓ Регулярное выражение корректно обрабатывает логи Xray!")
    
    print_info("\n📝 Актуальный формат access логов Xray:")
    print("  Формат: from [tcp:|udp:]IP:PORT accepted ... email: UID.username")
    print("  Пример: from 188.243.10.156:49322 accepted tcp:example.com:443 email: 35.user")
    print("\n  Если ваши логи отличаются, обновите ACCESS_LOG_RE в _runner.py!")


def check_xray_runner():
    """Проверяет логику обработки логов в _runner.py"""
    print_header("3. Проверка логики обработки логов в коде")
    
    runner_path = Path("marznode/backends/xray/_runner.py")
    if not runner_path.exists():
        print_error(f"Файл {runner_path} не найден")
        return
    
    try:
        with open(runner_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Проверка наличия _handle_log_line
        if '_handle_log_line' in content:
            print_success("Метод _handle_log_line найден в коде")
        else:
            print_error("Метод _handle_log_line не найден!")
        
        # Проверка наличия _last_meta
        if '_last_meta' in content:
            print_success("Словарь _last_meta для хранения метаданных найден")
        else:
            print_error("Словарь _last_meta не найден!")
        
        # Проверка вызова _handle_log_line
        if 'self._handle_log_line(line)' in content:
            print_success("Метод _handle_log_line вызывается при обработке логов")
        else:
            print_warning("Вызов _handle_log_line не найден в __capture_process_logs")
        
    except Exception as e:
        print_error(f"Ошибка при чтении файла: {e}")


def check_backend_integration():
    """Проверяет интеграцию с бэкендом"""
    print_header("4. Проверка интеграции с backend")
    
    backend_path = Path("marznode/backends/xray/xray_backend.py")
    if not backend_path.exists():
        print_error(f"Файл {backend_path} не найден")
        return
    
    try:
        with open(backend_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Проверка метода get_users_meta
        if 'def get_users_meta' in content or 'async def get_users_meta' in content:
            print_success("Метод get_users_meta найден в XrayBackend")
        else:
            print_error("Метод get_users_meta не найден в XrayBackend!")
        
        # Проверка вызова get_last_meta
        if 'get_last_meta' in content:
            print_success("Вызов get_last_meta() для получения метаданных из логов найден")
        else:
            print_error("Вызов get_last_meta() не найден!")
        
    except Exception as e:
        print_error(f"Ошибка при чтении файла: {e}")


def check_service_integration():
    """Проверяет интеграцию со службой"""
    print_header("5. Проверка интеграции с gRPC сервисом")
    
    service_path = Path("marznode/service/service.py")
    if not service_path.exists():
        print_error(f"Файл {service_path} не найден")
        return
    
    try:
        with open(service_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Проверка FetchUsersStats
        if 'def FetchUsersStats' in content or 'async def FetchUsersStats' in content:
            print_success("Метод FetchUsersStats найден в MarzService")
        else:
            print_error("Метод FetchUsersStats не найден!")
        
        # Проверка вызова get_users_meta
        if 'get_users_meta' in content:
            print_success("Вызов get_users_meta() в FetchUsersStats найден")
        else:
            print_warning("Вызов get_users_meta() не найден в FetchUsersStats")
        
        # Проверка remote_ip в ответе
        if 'remote_ip' in content:
            print_success("Поле remote_ip используется в ответе UserStats")
        else:
            print_warning("Поле remote_ip не найдено в коде")
        
    except Exception as e:
        print_error(f"Ошибка при чтении файла: {e}")


def provide_recommendations():
    """Предоставляет рекомендации по исправлению"""
    print_header("📋 Рекомендации по исправлению")
    
    print(f"{Colors.BOLD}Если устройства не отслеживаются, выполните следующие шаги:{Colors.ENDC}\n")
    
    print(f"{Colors.OKBLUE}1. Настройте access логи в конфигурации Xray:{Colors.ENDC}")
    print('   Добавьте или измените секцию "log" в xray_config.json:')
    print('   {')
    print('     "log": {')
    print('       "loglevel": "warning",')
    print('       "access": "/var/log/xray/access.log"')
    print('     }')
    print('   }')
    
    print(f"\n{Colors.OKBLUE}2. Убедитесь, что Xray имеет права на запись логов:{Colors.ENDC}")
    print('   sudo mkdir -p /var/log/xray')
    print('   sudo chown -R $(whoami) /var/log/xray')
    
    print(f"\n{Colors.OKBLUE}3. Перезапустите marznode после изменения конфигурации:{Colors.ENDC}")
    print('   # Перезапуск через docker-compose')
    print('   docker-compose restart')
    print('   # или')
    print('   systemctl restart marznode')
    
    print(f"\n{Colors.OKBLUE}4. Проверьте логи после подключения пользователя:{Colors.ENDC}")
    print('   tail -f /var/log/xray/access.log')
    print('   # Вы должны увидеть строки с email и IP адресами')
    
    print(f"\n{Colors.OKBLUE}5. Проверьте формат логов:{Colors.ENDC}")
    print('   Логи должны содержать информацию вида:')
    print('   "email: 123.username from 192.168.1.1:12345"')
    print('   Если формат другой, нужно обновить регулярное выражение в _runner.py')
    
    print(f"\n{Colors.OKBLUE}6. Проверьте версию Xray:{Colors.ENDC}")
    print('   xray version')
    print('   # Формат логов может отличаться в разных версиях')
    
    print(f"\n{Colors.WARNING}7. Для отладки включите DEBUG режим:{Colors.ENDC}")
    print('   В config.py или через переменную окружения:')
    print('   export DEBUG=true')


def main():
    print(f"{Colors.BOLD}{Colors.HEADER}")
    print("=" * 70)
    print("  Диагностика отслеживания устройств в marznode")
    print("=" * 70)
    print(f"{Colors.ENDC}")
    
    print_info("Этот скрипт проверит конфигурацию и код для выявления проблем")
    print_info("с отслеживанием IP адресов и устройств пользователей.\n")
    
    # Выполнение проверок
    config_ok = check_xray_config()
    check_access_log_format()
    check_xray_runner()
    check_backend_integration()
    check_service_integration()
    provide_recommendations()
    
    # Итоговое резюме
    print_header("📊 Итоговое резюме")
    
    if not config_ok:
        print_error(
            "ГЛАВНАЯ ПРОБЛЕМА: Access логи не настроены в конфигурации Xray!\n"
            "Без них отслеживание устройств невозможно.\n"
            "Следуйте рекомендациям выше для исправления."
        )
    else:
        print_success(
            "Конфигурация выглядит правильно.\n"
            "Если устройства все еще не отслеживаются, проверьте:\n"
            "  1. Реальное содержимое access логов после подключения\n"
            "  2. Соответствие формата логов регулярному выражению\n"
            "  3. Права доступа к файлам логов"
        )


if __name__ == "__main__":
    main()

