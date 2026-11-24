# Етап 1: Deye/Teltonika, безпечний моніторинг

## Зміни

- Усі Modbus-функції, крім FC03/FC04, блокуються в Device.execute.
- Прибрано сервіси запису, керувальні сутності та сканування локальної мережі.
- Транспорт тільки modbus_tcp, для двох MPPT і трьох фаз.
- Окремий Docker Compose із доступом до HA через localhost.
- Документація українською, GitHub Actions і регресійні тести.

## Приймання

- [ ] Python compileall PASS
- [ ] unittest PASS
- [ ] docker compose config PASS
- [ ] GitHub Actions PASS
- [ ] Code review та відсутність секретів у Git
- [ ] Наступним етапом: апаратні тести Modbus/Teltonika, WireGuard, архів, дашборд

Відсутній реальний доступ до Deye й Teltonika: розгортання в цей етап не входить.
Не зливати PR до успішного рев'ю і CI.
