# Етап 1: Deye/Teltonika, безпечний моніторинг

## Зміни

- Усі Modbus-функції, крім FC03/FC04, блокуються в Device.execute.
- Прибрано сервіси запису, керувальні сутності та сканування локальної мережі.
- Транспорт тільки modbus_tcp, для двох MPPT і трьох фаз.
- Окремий Docker Compose із доступом до HA через localhost.
- За результатами аудиту сервера Odoo 18 додано обмеження ресурсів контейнера,
  відключено автоматичний перезапуск для пробного режиму і додано read-only
  скрипт `deploy/preflight_capacity.py` (NO-GO при MemAvailable < 1536 MiB).
- Документація українською, GitHub Actions і регресійні тести.

## Приймання

- [x] Python compileall PASS — GitHub Actions 24.11.2026
- [x] unittest PASS (8 тестів) — GitHub Actions 24.11.2026
- [x] docker compose config PASS — GitHub Actions 24.11.2026
- [ ] GitHub Actions PASS — фінальний коміт має пройти повторне CI, включно з Hassfest
- [ ] Code review та відсутність секретів у Git
- [ ] Наступним етапом: апаратні тести Modbus/Teltonika, WireGuard, архів, дашборд

У форку немає GitHub topics, Issues та власних brand-ресурсів, тому HACS-перевірка публікації каталогу не застосовна до цього приватно використовуваного моніторингу і вимкнена для форка. Встановлення виконується через Docker, не через HACS.

Відсутній реальний доступ до Deye й Teltonika: розгортання в цей етап не входить.
Не зливати PR до успішного рев'ю і CI.
