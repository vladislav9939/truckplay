# Архитектура TruckPlay

```
truckplay.py (exe)          app.html
   ├─ HTTP сервер 127.0.0.1:47615 ──► страница интерфейса
   │     /config, /events, /cmd, /api/library, /api/telemetry, /media
   ├─ pywebview (WebView2) — окно без рамки, always-on-top
   ├─ Win32: WS_EX_TRANSPARENT (сквозная мышь), SetWindowPos, горячие клавиши
   └─ telemetry.py ── читает %LOCALAPPDATA%\TruckPlay\telemetry.json
                                      ▲
                                      │ пишет 10 раз/с
              plugin/truckplay_telemetry.dll (SCS SDK) ── игра
```

## Окно

* `pywebview.create_window(..., frameless=True, on_top=True, focus=False)` —
  окно без рамки, не забирает фокус у игры при старте.
* Размеры и координаты считаются в физических пикселях
  (`shcore.SetProcessDpiAwareness`), иначе при масштабе Windows 125/150% окно
  уезжает и становится крупнее.
* Сквозная мышь: `WS_EX_TRANSPARENT` на окне; в этом режиме клики уходят в игру.
* Восстановление: сторож каждые 150 мс проверяет, не свернули ли окно
  (Win+D), не потерялся ли `WS_EX_TOPMOST`, и поднимает его обратно.

## Интерфейс

Одна страница `app.html` без сборки: чистый HTML/CSS/JS.

* Экран обновляется опросом `GET /events?since=N` каждые 250 мс — горячие клавиши
  кладут команды в очередь на стороне Python, страница их забирает.
* Кнопки интерфейса дергают `GET /cmd?name=...` (открыть YouTube, переключить мышь,
  выход) и `GET /api/settings?...` (размер, позиция, темы, тумблеры).
* Музыка и радио играют через `<audio>`, файлы отдаёт `/media` с поддержкой Range.
* YouTube — IFrame API; для выбора ролика открывается отдельное окно с youtube.com,
  а ссылка ловится через `get_current_url()` и переезжает в основной экран.

## Телеметрия

Плагин `plugin/truckplay_telemetry.cpp` — обычный плагин SCS Telemetry SDK 1.16:

* `scs_telemetry_init` проверяет версию API, регистрирует каналы и события;
* интересующие каналы: `truck.speed`, `truck.engine.rpm`, `truck.engine.gear`,
  `truck.fuel.amount`, `truck.fuel.range`, `truck.adblue`, `truck.odometer`,
  `truck.cruise_control`, `truck.wear.*`, `truck.navigation.*`, `trailer.connected`;
* конфигурация: `truck` (ёмкость бака и AdBlue) и `job` (груз, масса, города, компания,
  доход, дедлайн, планируемый пробег);
* в `frame_end` (не чаще 10 раз в секунду) собирается JSON и атомарно заменяет
  `%LOCALAPPDATA%\TruckPlay\telemetry.json`.

Почему файл, а не разделяемая память: на реальной машине `MapViewOfFile` для
созданной плагином секции возвращал ошибку при инициализации SDK, и весь плагин
отключался. Файл с атомарной заменой работает всегда и его можно посмотреть глазами.

Сборка плагина: `plugin\build.bat` (MSVC x64 + заголовки SDK).

## Roadmap

- [x] окно без рамки, always-on-top, сквозная мышь
- [x] YouTube / радио / музыка
- [x] телеметрия через свой плагин
- [x] настройки: размер, позиция, темы, тумблеры
- [ ] проецирование интерфейса в экран машины: хук D3D11 + проекция мировой матрицы
      экрана в экранные координаты (как в CarPlay-модах) — тогда экран будет
      «прибит» к кабине и уезжать при повороте головы
- [ ] навигация по маршруту игры (линия маршрута из `navigation` каналов)
- [ ] пресеты положения под популярные кабины (DAF XG/XD, Scania NG, Volvo FH)
