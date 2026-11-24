# Deye SUN-14/15/16/18/20K-SG05LP3-EU-SM2 → Teltonika RS485 / 4G → Home Assistant

## Межі та архітектура

Форк є інтеграцією Home Assistant, а не окремим вебсервером. Перший етап
містить тільки код, тести й підготовку до розгортання; інвертор, Teltonika,
VPN та робочі сервери залишаються незмінними.

```text
Deye Modbus RJ45 RS485 → Teltonika (Modbus TCP ⇄ RTU)
                    → 4G / CGNAT → WireGuard VPN → Linux / Docker / Home Assistant
```

Використовуйте саме Modbus TCP ↔ RTU gateway, не прозорий Serial-over-IP.
Прямий TCP-доступ до шлюзу з Інтернету заборонений. SIM з публічною IP
не потрібна: Teltonika встановлює вихідне VPN-з'єднання.

## Порти та фізичний зв'язок

На сторінці 49 (друкована нумерація) наданого посібника Deye для
`Modbus RJ45` зазначено: 1 = 485_B, 2 = 485_A, 3/6 = GND, 7 = 485_A,
8 = 485_B. Це **не** `Meter-485` і не `BMS`.
Основна частина посібника водночас називає Modbus порт зарезервованим;
підтримку потрібно підтвердити саме на вашому пристрої.

Раніше виникала суперечність TRB145 проти коду TRB140.
Перевірте точну фізичну модель і офіційну розпіновку перед монтажем.
Кабель, гальванічне розділення, землю, термінацію та живлення перевіряє
кваліфікований монтажник. Нічого не перепідключайте під напругою.

## Захист

- VPN (приклад Teltonika 10.77.45.2, сервер 10.77.45.1).
- Дозволити TCP 1502 шлюзу лише від VPN-адреси сервера; не з мобільного WAN.
- Home Assistant слухає виключно 127.0.0.1:8123 на Linux-хості.
- Доступ до вебпанелі — SSH-тунель або окремий захищений HTTPS reverse proxy.
- WireGuard keys, паролі, `deploy/data` та `deploy/.env` не зберігати в Git.
- Інтеграція допускає лише Modbus FC03/FC04. Це запобіжник у застосунку,
  а не апаратна заборона запису з інших Modbus-клієнтів.

## Розгортання

Потрібні Docker Engine, Docker Compose Plugin та окремий Linux-хост.

### Важливо: сервер ergodata-lnx (Debian 13, 4 GiB, Odoo 18)

Аудит 24.11.2026 виявив ~1,2 GiB MemAvailable, шість процесів
`odoo18.service` (разом ~1,54 GiB PSS) та відсутність активного
swap-in/swap-out. Наразі **не запускайте** Home Assistant на цьому сервері
без збільшення запасу оперативної пам'яті або окремої VM: роботу Odoo
зупиняти або оптимізувати без окремого плану не можна.

Стандартний Compose містить безпечні для ПРОБНОГО запуску обмеження
`mem_limit=768m`, `memswap_limit=768m`, `cpus=1.0`, `pids_limit=160`,
`restart=no` та ротацію журналів. Обмеження контейнера **не гарантує**
відсутності впливу на інші служби, а 768 MiB може виявитися замало для
самого Home Assistant: у цьому разі контейнер зупиниться. Не змінюйте
`restart` на `unless-stopped` до стабільного випробування.

### Перевірка без запуску

```bash
python3 deploy/preflight_capacity.py
docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --quiet
```

Порогове значення preflight — 1536 MiB `MemAvailable`, це мінімум
для **оцінки** пробного запуску, а не підтвердження безпеки. Для
постійної експлуатації рекомендовано 8 GiB ОЗП на наявному сервері
або окрема VM з 4 GiB ОЗП.

### Контрольований пробний запуск після позитивного preflight

```bash
cd deploy
cp -n .env.example .env
mkdir -p data
docker compose config --quiet
docker compose pull homeassistant
docker compose up -d --no-deps homeassistant
docker compose ps
docker stats --no-stream deye-homeassistant
docker compose logs --tail=100 homeassistant
```

Якщо Odoo деградує, зростає PSI, починається swap-in/out чи HA
виходить за ліміт, негайно виконайте `cd deploy && docker compose down`.
Не використовуйте `docker system prune` або видалення томів Odoo.
Ресурси фіксуйте до/після запуску: `free -h`, `vmstat 1 5`,
`cat /proc/pressure/memory`, `docker stats --no-stream`.

Локальний доступ через SSH: `ssh -L 8123:127.0.0.1:8123 USER@SERVER`
і `http://127.0.0.1:8123` у браузері.

Не встановлюйте два компоненти з доменом `solarman` в одному Home Assistant.
Не запускайте цей стек паралельно з наявним HA без перевірки конфліктів портів.

## Початкові параметри інтеграції

У Home Assistant відкрийте `Settings → Devices & services → Add integration`
і виберіть Solarman:

| Поле | Початкове значення |
|---|---|
| Host | VPN IP Teltonika, наприклад 10.77.45.2 |
| Port | TCP 1502 — лише якщо так налаштовано шлюз |
| Transport | modbus_tcp |
| Lookup file | deye_p3.yaml, вручну |
| Modbus Slave ID | 1, перевірити |
| MPPT | 2 |
| Phases | 3 |
| Battery packs | -1 |

9600, 8N1, Slave ID 1 є початковими припущеннями, а не верифікованими
параметрами прошивки. Профіль deye_p3.yaml перевірявся на інших моделях;
точні регістри SG05LP3-EU-SM2 потрібно звірити з LCD інвертора.

## Приймання й відкат

1. Python compileall і unittest мають завершитися PASS.
2. GitHub Actions мають пройти успішно.
3. `docker compose config` має відпрацювати без запуску.
4. VPN handshake і TCP перевіряються до Modbus.
5. SOC, PV, GRID, LOAD і GEN порівнюються з фізичним інвертором.
6. Відсутні керувальні сутності й сервіси запису; секретів у Git немає.
7. Фактичну модель Teltonika і розпіновку встановлено без припущень.

Для відкату Docker: `cd deploy && docker compose down`, без видалення
`deploy/data`. Основна гілка GitHub залишається незмінною до успішного PR.
