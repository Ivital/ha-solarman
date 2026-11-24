# Deye SUN-14/15/16/18/20K-SG05LP3-EU-SM2 → Teltonika RS485 / 4G → Home Assistant

## Межі та архітектура

Форк є інтеграцією Home Assistant, а не окремим вебсервером. Перший етап
містить тільки код, тести й підготовку до розгортання; інвертор, Teltonika,
VPN та робочі сервери залишаються незмінними.

```text
Deye Modbus RJ45 RS485 → Teltonika (Modbus TCP ⇄ RTU)
                    → 4G → наявний VPN-сервер MikroTik
                    → локальна мережа → Debian 192.168.5.31 / Docker / Home Assistant
```

Використовуйте саме Modbus TCP ↔ RTU gateway, не прозорий Serial-over-IP.
У користувача вже є VPN-сервер MikroTik, до якого Teltonika підключається
як VPN-клієнт; доступ до Teltonika здійснюється за її приватною адресою.
Тип VPN і конкретні адреси ще не підтверджені. На Debian другий VPN
розгортати не потрібно. SIM з публічною IP не потрібна, якщо Teltonika
встановлює вихідне VPN-з'єднання. Прямий доступ до Modbus з WAN заборонений.

Перед налаштуванням опитування перевірте маршрут із Debian до Teltonika:
```bash
read -rp "Private VPN IP Teltonika: " TELTONIKA_IP
ip -4 route get "$TELTONIKA_IP"
ping -c 2 -W 2 "$TELTONIKA_IP" || true
```
За відсутності ICMP-відповіді потрібна окрема перевірка TCP-порту шлюзу;
ICMP може бути заблоковано. Не додавайте статичних маршрутів навмання.
Якщо доступу з Debian немає, перевіряються маршрут назад до 192.168.5.31
та правила MikroTik firewall `forward`. Паролі, ключі й конфігураційний
експорт MikroTik у Git не завантажувати.

## Перевірка наявного VPN і Modbus TCP (24.11.2026)

На Debian `192.168.5.31` підтверджено маршрут через MikroTik `192.168.5.10`
до Teltonika `192.168.89.106`: ICMP 2/2 без втрат і TCP connect до порту
`50006` успішний. Це підтверджує **мережеву** доступність, але **не**
доказ підтримки MBAP/Modbus TCP або відповідей інвертора.

За документацією Teltonika є різні режими:

- **Services → Modbus → Modbus TCP over Serial Gateway**: очікує MBAP
  (Modbus TCP), переводить запити в Modbus RTU на RS485.
- **Services → Serial Utilities → Over IP**: прозора пересилка байтів
  через TCP. Тут `modbus_tcp` не працює без спеціального перетворення
  транспортного формату.

На конкретному пристрої перевірити точний розділ вебінтерфейсу, ім'я
сервісу, порт 50006, `Server ID`, швидкість, парність і режим RS485.
Через попередню суперечність TRB145/TRB140 перевірити модель апаратно.
**До цієї перевірки нічого на Teltonika не перемикати.**

Для одного контрольованого запиту FC03 (рег. `0x0000` із профілю
`deye_p3.yaml`; сумісність із SG05LP3 ще не доведена):

```bash
python3 tools/deye_modbus_tcp_probe.py --host 192.168.89.106 --port 50006
# Лише після підтвердження режиму шлюзу:
python3 tools/deye_modbus_tcp_probe.py --host 192.168.89.106 --port 50006 --execute
```

Скрипт не записує регістри, не сканує пристрої й не робить повторних
запитів. Відповідь `MODBUS_TCP_FC03_PASS` доводить читання одного
регістру; `MODBUS_TCP_EXCEPTION` доводить відповідь у форматі Modbus TCP,
але не гарантує зв'язок із інвертором. Таймаут або неправильний MBAP
не доводить, що пристрій не підтримує Modbus: слід уточнити режим шлюзу,
Slave ID та серійні параметри.

Офіційні джерела:
- https://wiki.teltonika-networks.com/view/TRB145_Modbus
- https://wiki.teltonika-networks.com/view/TRB145_Serial_Utilities

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

- Використати наявний VPN-сервер MikroTik і чинного VPN-клієнта Teltonika.
- Дозволяти підключення до TCP-порту Modbus-шлюзу тільки з Debian
  192.168.5.31 через MikroTik та VPN; номер порту потрібно підтвердити
  в налаштуваннях Teltonika. Не відкривати порт на WAN.
- Home Assistant слухає виключно 127.0.0.1:8123 на Linux-хості.
- Доступ до вебпанелі — SSH-тунель або окремий захищений HTTPS reverse proxy.
- VPN-ключі, паролі MikroTik/Teltonika, `deploy/data` та `deploy/.env`
  не зберігати в Git.
- Інтеграція допускає лише Modbus FC03/FC04. Це запобіжник у застосунку,
  а не апаратна заборона запису з інших Modbus-клієнтів.

## Розгортання

Потрібні Docker Engine, Docker Compose Plugin та окремий Linux-хост.

### Важливо: сервер ergodata-lnx (Debian 13, 8 GiB, Odoo 18)

24.11.2026 VM `ERGODATA-LNX` на Hyper-V `DCKSHZ` штатно вимкнено, обсяг
статичної ОЗП збільшено з 4 до 8 ГіБ і VM успішно запущено. Після запуску
`MemAvailable` ≈ 6,7 ГіБ, swap 0 B, `odoo18` і `nginx` активні,
`systemctl --failed` = 0; `preflight_capacity.py` повідомив PASS
(6888 MiB доступно, 37,3 GiB на диску). Ці результати НЕ є перевіркою
пікового навантаження Odoo, а вебдоступність Ergodata ще потрібно перевірити.

Після збільшення ОЗП для **контрольованого пробного запуску** встановлено
ліміти `mem_limit=1536m`, `memswap_limit=1536m`, `cpus=1.0`,
`pids_limit=160`, `restart=no`, ротацію журналів та доступ до вебпанелі
тільки через `127.0.0.1:8123`. Відсутність контейнерного swap не дає
Home Assistant безконтрольно витісняти Odoo. Водночас обмеження
**не гарантують** відсутності впливу на інші служби: перевіряйте CPU,
пам'ять, PSI й стан Odoo. Якщо для Home Assistant 1536 МБ замало —
не збільшуйте ліміти автоматично; спочатку аналіз журналів і навантаження.
Не змінюйте `restart` на `unless-stopped` до стабільного випробування.

### Перевірка без запуску

```bash
python3 deploy/preflight_capacity.py
docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --quiet
```

Порогове значення preflight — 1536 MiB `MemAvailable`, це мінімум
для **оцінки** пробного запуску, а не підтвердження безпеки.
VM уже має 8 ГіБ, але під навантаженням Odoo необхідне додаткове
спостереження.

### Усунення помилки першого старту (24.11.2026)

Перший запуск образу Home Assistant завершився повідомленням
`s6-overlay-suexec: fatal: can only run as pid 1`, оскільки Compose
помилково містив `init: true` (Docker tini перехоплював PID 1).
Виправлено на `init: false` і додано регресійний тест. Попередній
контейнер треба **перестворити**, а не просто перезапустити:

```bash
cd ~/deye-monitoring-src
git pull --ff-only
sudo docker compose -f deploy/compose.yaml --env-file deploy/.env config --quiet
cd deploy
sudo docker compose up -d --no-deps --force-recreate homeassistant
sudo docker compose ps -a
sudo docker compose logs --tail=100 homeassistant
```

Команда не запускає опитування інвертора. Якщо після виправлення
залишаються помилки прав доступу `s6` або `/config`, зафіксуйте журнал
і не вмикайте `privileged: true` чи `network_mode: host` навмання.

### Виправлення доступу до /config (24.11.2026)

Після вимкнення Docker init Home Assistant завершився з `Exit=1`:
`PermissionError: [Errno 13] Permission denied: '/config/.ha_run.lock'`.
`cap_drop: ALL` забирає `CAP_DAC_OVERRIDE`, тому root-процес контейнера
не може писати в каталог `deploy/data`, створений власником `ivital`
з правами `0755`. Не надавайте `chmod 777` і не додавайте
`privileged: true` чи `CAP_DAC_OVERRIDE` для обходу захисту.

Скрипт виправляє **тільки сам каталог** `deploy/data`, не змінюючи
файли, Docker volume інших систем та права рекурсивно. Він також звіряє
точний шлях bind mount контейнера, вимагає зупинений контейнер і має
режим перевірки без змін:

```bash
cd ~/deye-monitoring-src
git pull --ff-only
bash deploy/repair_config_permissions.sh --check
bash deploy/repair_config_permissions.sh --apply
cd deploy
sudo docker compose up -d --no-deps --force-recreate homeassistant
sudo docker compose ps -a
sudo docker compose logs --tail=100 homeassistant
```

Доступ до `deploy/data` на хості після зміни потребуватиме `sudo`;
це бажано для секретів Home Assistant. Якщо з'являться помилки прав
для **вкладених файлів** або для інших шляхів, перевіряйте конкретний
файл окремо, а не виконуйте рекурсивне `chown` чи `chmod`.

### Контрольований пробний запуск після позитивного preflight

```bash
cd ~/deye-monitoring-src
git pull --ff-only
python3 deploy/preflight_capacity.py
cd deploy
test ! -e .env && cp .env.example .env || echo "Using existing .env; inspect current limits"
mkdir -p data
docker compose config --quiet
sudo docker compose pull homeassistant
sudo docker compose up -d --no-deps homeassistant
sudo docker compose ps
sudo docker stats --no-stream deye-homeassistant
sudo docker compose logs --tail=100 homeassistant
```

Якщо Odoo деградує, зростає PSI, починається swap-in/out чи HA
виходить за ліміт, негайно виконайте `cd deploy && sudo docker compose down`
(тільки з каталогу цього проєкту).
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
| Host | Фактична приватна VPN-адреса Teltonika (не припускати конкретну IP) |
| Port | Фактичний TCP-порт Modbus TCP ↔ RTU шлюзу на Teltonika |
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
4. Чинний VPN MikroTik↔Teltonika, маршрути Debian↔Teltonika та TCP шлюзу перевіряються до Modbus.
5. SOC, PV, GRID, LOAD і GEN порівнюються з фізичним інвертором.
6. Відсутні керувальні сутності й сервіси запису; секретів у Git немає.
7. Фактичну модель Teltonika і розпіновку встановлено без припущень.

Для відкату Docker: `cd deploy && docker compose down`, без видалення
`deploy/data`. Основна гілка GitHub залишається незмінною до успішного PR.
