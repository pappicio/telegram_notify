# Telegram Notify for OpenCreator

A Telegram notification and printer-monitoring plugin for OpenCreator and
Flashforge Creator 5.

This edition provides a standalone web interface and Python service. It does
not require Z-Mod and does not install the original Z-Mod Moonraker component.

## Project lineage and credits

This project is based on and adapted from the upstream project:

- **Original project:** [mlengle/telegram_notify](https://github.com/mlengle/telegram_notify)
- **Original maintainer / repository:** [mlengle](https://github.com/mlengle)
- **Original target:** Z-Mod firmware for Flashforge printers
- **Original purpose:** Telegram print notifications without the Apprise
  library.
- **Original license:** GNU General Public License v3.0 (GPL-3.0)

The original project sends Telegram messages directly from Moonraker to the
Telegram Bot API. Its README reports the following memory measurements on a
Flashforge AD5M running Z-Mod:

| Configuration | Moonraker RssAnon |
| --- | ---: |
| notify (Apprise) | 30.3 MB |
| telegram_notify | 24.7 MB |
| No notifications | 24.2 MB |

The upstream project reports approximately 5.6 MB less memory usage than
`notify`, with the original plugin adding approximately 0.5 MB.

These are upstream measurements, not benchmarks of this OpenCreator edition.

The original Z-Mod ecosystem is maintained separately:
[ghzserg/zmod](https://github.com/ghzserg/zmod).

## Original upstream functionality

The upstream Z-Mod plugin provides:

- Print started, completed, failed and cancelled notifications.
- Print pause and resume notifications.
- Print progress percentage updates.
- Webcam snapshots in Telegram messages.
- Z-Mod messages, including filament sensor and printer power-on events.
- Multiple chat IDs, including Telegram forum topics.
- Configurable notification text, silent messages and snapshot options.
- A message queue and Telegram rate-limit handling.

The original implementation was designed specifically for Telegram.
Other notification services are not supported; the upstream `notify` plugin
is intended for those services.

### Original Z-Mod compatibility notes

The original README warns not to enable `notify` and `telegram_notify`
simultaneously in Z-Mod, because the `_NOTIFY` macro is shared.

The original Z-Mod configuration and macros below are documented for
upstream users. They are not installation instructions for this standalone
OpenCreator edition.

Original Z-Mod commands:

- `TELEGRAM_NOTIFY MSG="text" PHOTO=1`
- `_NOTIFY MSG="text" PHOTO=0 TYPE=...`
- `SAVE_VARIABLE VARIABLE=notify_percent VALUE=5`
- `SAVE_VARIABLE VARIABLE=notify_photo VALUE=0`

Original Z-Mod installation used:

`ENABLE_PLUGIN name=telegram_notify`

Original Z-Mod removal used:

`DISABLE_PLUGIN name=telegram_notify`

## OpenCreator edition

The OpenCreator edition provides its own web interface, configuration,
authentication and monitoring service.

### Features

- Web interface for Telegram bot configuration.
- Local Creator account authentication.
- Session restoration with a 30-minute session lifetime.
- English and Italian interface languages.
- Multiple Telegram chat IDs and forum topic IDs.
- Test message functionality.
- Print event notifications.
- Optional webcam snapshots for print notifications.
- Creator 5 startup notification, including the dynamically detected IP.
- Klipper/Moonraker readiness monitoring.
- Printer status, current file and print progress monitoring.
- Enable/disable monitoring without disabling the web interface.
- Automatic SOC/Tunneled mode detection.

### SOC and Tunneled behavior

The monitoring service is active only when both conditions are true:

1. **Enable service** is enabled in the web interface.
2. The printer is in SOC mode.

The mode is detected using the executable permission bits of:

`/usr/data/opencreator/tunneled/tunneled.sh`

- `644` — SOC mode.
- `755` — Tunneled mode.

In Tunneled mode, or when the service is disabled, Telegram monitoring
remains dormant. The web interface remains available.

If mode detection fails, monitoring remains dormant as a safety measure.

The test-message endpoint follows the same SOC and Enable service checks.

## Installation and service management

Install and manage the plugin through the OpenCreator plugin manager.

**To install Telegram Notify:**

1. Open an SSH shell on your Creator 5.
2. Run the following command:

```sh
oc-plugin install https://github.com/pappicio/telegram_notify
```

**Plugin ID:**

`telegram_notify`

**Useful commands:**

```
sh
oc-plugin status telegram_notify
oc-plugin restart telegram_notify
oc-plugin stop telegram_notify
oc-plugin update telegram_notify
oc-plugin remove telegram_notify
```

Web interface:

`http://<CREATOR_IP>:8767/`

The IP address is the current Creator 5 address.

### Runtime requirements

- OpenCreator plugin manager.
- Creator 5 environment.
- Python at `/usr/data/python-hf/bin/python3`.
- Moonraker available locally at `http://127.0.0.1:7125`.
- Network access to the Telegram Bot API.

## Telegram setup

1. Create a Telegram bot using [@BotFather](https://t.me/BotFather).
2. Obtain the bot token.
3. Send the bot a message.
4. Obtain the chat ID. The upstream project suggests
   [@userinfobot](https://t.me/userinfobot).
5. Enter the token and chat ID in the OpenCreator web interface.
6. Save the configuration and use the test-message function.

Multiple chat IDs can be separated by commas. Forum topics can use:

`chat_id:topic_id`

Example:

`-1001234567890, -1009876543210:42`

Keep the bot token private. Do not publish configuration files or logs
containing credentials.

## Webcam snapshots

The OpenCreator edition can attach a webcam snapshot to supported print
notifications when photo sending is enabled and a valid snapshot URL is
configured.

The original upstream project also supports webcam snapshots. Its README
documents Telegram's caption and message limits.

## Original Telegram limits

The upstream README documents:

- Text messages are limited to 4096 characters.
- Snapshot captions are limited to 1024 characters.
- If Telegram requests a delay because of rate limits, the original plugin
  waits and retries once.
- Messages are sent sequentially.
- When the original queue exceeds `max_queue`, new messages are dropped and
  a warning is written to `moonraker.log`.

These details describe the upstream implementation; behavior in the
OpenCreator edition depends on its current implementation.

## File layout

```text
telegram_notify/
├── app/
│   └── telegram_notify.py
├── backup/
├── data/
│   ├── config.json
│   └── monitor-state.json
├── logs/
├── scripts/
│   ├── install.sh
│   ├── start.sh
│   ├── update.sh
│   └── uninstall.sh
├── web/
│   ├── index.html
│   └── languages/
│       ├── en.json
│       └── it.json
├── LICENSE
├── NOTICE
├── README.md
└── opencreator-plugin.json
```

Configuration and runtime state are stored inside the plugin directory.
The configuration file contains sensitive bot credentials and must be kept
private.

## Updating

The OpenCreator update hook runs `scripts/update.sh`, which delegates to
`install.sh` to ensure the plugin's required directories exist.

It does not overwrite the configuration, Telegram token or monitor state.

Use:

`oc-plugin update telegram_notify`

## Uninstallation

The OpenCreator uninstall hook does not remove external Moonraker or Klipper
configuration because this edition does not install those integrations.

To remove the plugin:

`oc-plugin remove telegram_notify`

To ask the plugin manager to preserve the plugin data folder:

`oc-plugin remove --keep-data telegram_notify`

The uninstall hook itself does not delete user data.

## License

This project is distributed under the GNU General Public License v3.0
(GPL-3.0). See [LICENSE](LICENSE) for the complete license text.

The original upstream license is preserved. Modifications and additions
for the OpenCreator edition are documented separately from the upstream
project. See [NOTICE](NOTICE) for attribution and modification information.
