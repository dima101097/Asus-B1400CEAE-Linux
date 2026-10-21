# laptop-fixes

Фікси спеціальних клавіш, сну та живлення для **ASUS ExpertBook B1400CEAE** під
**Linux Mint 22 (Cinnamon, X11)**. Кожен фікс вмикається й вимикається окремо, а вимкнення
повністю відкочує його зміни.

> Програма писалася й перевірялася на одному конкретному ноутбуці (BIOS `B1400CEAEY.328`,
> ядро 7.0, PipeWire 1.0, systemd 255). На іншому залізі вона може не працювати; інсталятор
> попередить, якщо модель інша.

## Фікси

### Клавіатура

| Фікс | Проблема | Що робить |
|---|---|---|
| `fnlock` | Стан Fn-lock (Fn+Esc) скидається після перезавантаження: драйвер `asus-wmi` щоразу ставить `fnlock_default=Y` | Служба стежить за Fn+Esc (kprobe на `asus_wmi_handle_event_code`, код `0x4E`) і записує стан у `/etc/modprobe.d`, тож драйвер відновлює його під час завантаження |
| `lock-key` | F9 надсилає Super+L, а Cinnamon на цю комбінацію відкриває Looking Glass | Знімає Super+L з Looking Glass і прив'язує її до блокування екрана |
| `screenshot-key` | F11 надсилає Super+Shift+S, на яку нічого не призначено | Знімок області в буфер обміну, як у Windows |
| `myasus-key` | F12 (MyASUS, `XF86Launch1`) нічого не робить | Відкриває вікно laptop-fixes; повторне натискання його закриває |

### Мікрофон і камера

| Фікс | Проблема | Що робить |
|---|---|---|
| `micmute` | Cinnamon вимикає лише типове джерело (мікрофон гарнітури), а світлодіод прив'язаний до вбудованого DMIC | Клавіша вимикає й вмикає **всі** мікрофони разом, показує OSD. Світлодіод загоряється лише тоді, коли вимкнені всі мікрофони |
| `noise-cancel` | Клавіша шумодаву (`XF86Tools` без Fn, `XF86Launch5` з Fn) нічого не робить | Шумозаглушення в обидва боки через WebRTC PipeWire: для мікрофона і для звуку, що відтворюється |
| `camera` | F10 (`XF86WebCam`) ніде не прив'язана | Вимикає й вмикає вебкамеру на рівні USB (`authorized=0/1`) з OSD |

### Живлення і сон

| Фікс | Проблема | Що робить |
|---|---|---|
| `battery-limit` | Немає ліміту заряду; BIOS після завантаження скидає його на 100% | Ставить `charge_control_end_threshold` (за замовчуванням 80%) під час завантаження і після сну |
| `deep-sleep` | Сон у режимі s2idle: вентилятор крутиться, батарея сідає | Перемикає `/sys/power/mem_sleep` на `deep` (S3) |
| `swapfile` | Swap немає | `/swapfile` потрібного розміру (за замовчуванням = RAM) і рядок у `/etc/fstab`; розмір можна змінювати |
| `hibernate` | Гібернація недоступна: немає swap і `resume=`, а Ubuntu забороняє її через polkit | `resume=`/`resume_offset=` для GRUB та initramfs і polkit-правило, яке дозволяє гібернацію |

Клавіші, що працюють і без фіксів: F1–F5 (звук, яскравість), F6 (тачпад), F8 (Super+P, монітори).

## Встановлення

```bash
cd laptop-fixes
./install.sh
```

Інсталятор копіює програму в `~/.local/share/laptop-fixes`, створює команду
`~/.local/bin/laptop-fixes`, пункт меню «Фікси клавіш ноутбука» та іконки, а потім пропонує
увімкнути всі фікси. Повторний запуск оновлює встановлену версію, зокрема копію для системних служб.

**Права root.** Фікси з позначкою 🔒 змінюють системні файли. Перед кожною такою зміною програма
показує, що саме зміниться, і питає підтвердження. Потім запитує пароль: `sudo` у терміналі або
`pkexec` у вікні. Без підтвердження root-зміни не робляться.

## Використання

```text
laptop-fixes list                 список фіксів (коротко)
laptop-fixes status               стан кожного фіксу та заліза
laptop-fixes enable  FIX...|all   увімкнути фікс(и)
laptop-fixes disable FIX...|all   вимкнути фікс(и) і повністю відкотити зміни
laptop-fixes gui                  вікно з перемикачами (або F12)
laptop-fixes fnlock-boot Y|N      вручну задати стан Fn-lock після завантаження
laptop-fixes battery-limit N      ліміт заряду батареї, % (20–100)
laptop-fixes swap-size N          розмір swap-файлу, ГіБ (1–128)
```

У вікні фікси розкладені по вкладках «Клавіатура», «Мікрофон і камера», «Живлення і сон».
Для ліміту заряду й розміру swap є поле зі значенням і кнопка «Застосувати».

## Видалення

```bash
./install.sh --uninstall
```

Вимикає всі фікси, відкочує їхні зміни (зокрема видаляє swap-файл, прибирає параметри з GRUB та
initramfs і повертає комбінації клавіш Cinnamon) і видаляє програму. Якщо root-частини не вдалося
вимкнути, програма не видаляється, щоб служби не лишилися без файлів.

## Що змінюється в системі

**Root-частини** (кожна створюється лише своїм фіксом):

| Фікс | Файли |
|---|---|
| `fnlock` | `/etc/systemd/system/laptop-fixes-fnlock.service`, `/etc/modprobe.d/laptop-fixes-fnlock.conf` |
| `micmute` | `/etc/systemd/system/laptop-fixes-micled.service` |
| `camera` | `/etc/udev/rules.d/70-laptop-fixes-camera.rules` |
| `battery-limit` | `/etc/systemd/system/laptop-fixes-battery.service`, `/usr/lib/systemd/system-sleep/laptop-fixes-battery` |
| `deep-sleep` | `/etc/systemd/system/laptop-fixes-deepsleep.service` |
| `swapfile` | `/swapfile`, рядок у `/etc/fstab` |
| `hibernate` | `/etc/default/grub.d/99-laptop-fixes-hibernate.cfg`, `/etc/initramfs-tools/conf.d/laptop-fixes-resume`, `/etc/polkit-1/rules.d/40-laptop-fixes-hibernate.rules` |
| спільне | `/usr/local/lib/laptop-fixes/` (копія програми для служб), `/var/lib/laptop-fixes/` (стан) |

**Користувацькі частини**: комбінації клавіш Cinnamon (`custom-list` і `media-keys`), стан у
`~/.config/laptop-fixes/state.json`. Початкові значення комбінацій, які фікс очищає, зберігаються
й повертаються під час вимкнення.

## Обмеження

- **Fn-lock** у програмі показується як `Y`/`N`, тобто значення параметра `fnlock_default` драйвера.
- **Камера** після перезавантаження знову ввімкнена, а **шумозаглушення** вимкнене: стан цих
  фіксів не зберігається між сеансами.
- **Шумозаглушення динаміків** налаштоване на голос: музика може звучати гірше. Звук іде на ті
  навушники чи динаміки, що були вибрані в момент увімкнення, і додається затримка близько 30 мс.
- **Гібернація** надійна, коли swap не менший за RAM. Під час зміни розміру swap `resume_offset`
  оновлюється автоматично.
- **Thunderbolt** не налаштовується: на цьому ноутбуці в системі немає контролера Thunderbolt (NHI),
  є лише USB-частина Thunderbolt 4 (USB-C до 10 Гбіт/с).

## Як знайдено коди клавіш

| Клавіша | Подія | Keycode | X11 |
|---|---|---|---|
| F6 тачпад | WMI `0x6b` | `KEY_F21` | `XF86TouchpadToggle` |
| F8 монітори | AT | Super+P | — |
| F9 замок | AT | Super+L | — |
| F10 камера | WMI `0x85` | `KEY_CAMERA` | `XF86WebCam` |
| F11 знімок | AT | Super+Shift+S | — |
| F12 MyASUS | WMI `0x86` | `KEY_PROG1` | `XF86Launch1` |
| мікрофон | WMI `0x7c` | `KEY_F20` | `XF86AudioMicMute` |
| шумодав | WMI (без Fn / з Fn) | `KEY_F13` / `KEY_F14` | `XF86Tools` / `XF86Launch5` |
| Fn+Esc | WMI `0x4E` | — (обробляє ядро) | — |

## Ліцензія

[MIT](LICENSE)
