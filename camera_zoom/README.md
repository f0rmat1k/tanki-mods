# camera_zoom — отдаление камеры

Камеру в обычном режиме (от третьего лица) можно отодвинуть дальше: до 50 м вместо 25 м.

Игра читает диапазон камеры из `avatar_input_handler.xml` (`arcadeMode/camera/distRange`) в `ArcadeCamera._readBaseCfg`. Мод сразу после этого поднимает максимум, и игра собирает рабочие настройки уже с ним.

Настройки: `mods\configs\camera_zoom\camera_zoom.json` — `enabled`, `max_distance` (25–100 м).

```bash
python build_small.py camera_zoom
```
