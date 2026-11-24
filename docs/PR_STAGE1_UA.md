# Етап 1: Deye/Teltonika, безпечний моніторинг

## Зміни

- Усі Modbus-функції, крім FC03/FC04, блокуються в Device.execute.
- Прибрано сервіси запису, керувальні сутності та сканування локальної мережі.
- Транспорт тільки modbus_tcp, для двох MPPT і трьох фаз.
- Окремий Docker Compose із доступом до HA через localhost.
- Документація українською, GitHub Actions і регресійні тести.

## Приймання

- [x] Python compileall PASS — GitHub Actions 24.11.2026
- [x] unittest PASS (8 тестів) — GitHub Actions 24.11.2026
- [ ] docker compose config PASS — додано окремий крок у CI, результат очікує повторного запуску
- [ ] GitHub Actions PASS — після оновлення цього PR
- [ ] Code review та відсутність секретів у Git
- [ ] Наступним етапом: апаратні тести Modbus/Teltonika, WireGuard, архів, дашборд

Відсутній реальний доступ до Deye й Teltonika: розгортання в цей етап не входить.
Не зливати PR до успішного рев'ю і CI.
