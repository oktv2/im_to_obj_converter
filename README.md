[README_RU.txt](https://github.com/user-attachments/files/30374695/README_RU.txt)
УНИВЕРСАЛЬНЫЙ TRAINZ / AURAN .IM → WAVEFRONT .OBJ
Версия 2.0
==================================================

Это новая версия конвертера, рассчитанная не на одну конкретную модель, а на
семейство Trainz/Auran Indexed Mesh с заголовком JIRF/IDXM.

БЫСТРЫЙ ЗАПУСК
--------------
1. Установите Python 3.10 или новее с сайта python.org.
   Во время установки включите «Add Python to PATH».
2. Не разносите BAT и im_to_obj_universal.py по разным папкам.
3. Перетащите на «ПЕРЕТАЩИТЬ_IM_CONFIG_ИЛИ_ПАПКУ.bat»:
   • один или несколько .im;
   • config.txt;
   • целую папку ассета.
4. Папка обходится рекурсивно. Один сбой не останавливает остальные файлы.

Можно положить весь комплект в корень ассета и запустить
«КОНВЕРТИРОВАТЬ_ВСЕ_IM_В_ЭТОЙ_ПАПКЕ.bat». Результаты будут собраны в
OBJ_EXPORT с сохранением исходной структуры подпапок.

ЧТО ПОДДЕРЖИВАЕТСЯ
------------------
• стандартная обёртка JIRF/IDXM;
• сторонние файлы с raw IDXM без JIRF;
• INFO 100 и новее;
• MATL 100/101 и 102/103, включая свойства и opacity;
• обычные и wide JET strings;
• GEOM 100, 101, 102, 103, 104, 200, 201;
• совместимые расширения новее 201 с тем же базовым устройством;
• GEOM 104 как с vertex colors, так и без них;
• GEOM 201 как с tangent data, так и без неё;
• triangles, lines и points;
• стандартные 16-bit indices и эвристический fallback для 32-bit indices;
• несколько UV-наборов — в OBJ экспортируется выбранный набор, по умолчанию 0;
• attachment points — сохраняются в *_attachments.csv;
• SKEL/INFL bone hierarchy;
• восстановление rigid parent-bone transforms для статического OBJ;
• пакетная конвертация с продолжением после повреждённого файла;
• частичное спасение читаемых CHNK из повреждённого IM;
• поиск texture.txt и исходных TGA/PNG/JPG/BMP/DDS/WEBP/TIFF;
• копирование найденных картинок в подпапку textures при запуске через BAT.

РЕЗУЛЬТАТ
---------
Для каждой модели создаются:
• <имя>.obj — геометрия;
• <имя>.mtl — материалы и ссылки на картинки;
• <имя>_attachments.csv — точки крепления, если они есть;
• <имя>_bones.csv — кости, если они есть;
• <имя>_report.txt — версии блоков, статистика и предупреждения.

Для всей операции создаётся:
• _im_to_obj_batch_report.txt — список успешных, пропущенных и ошибочных файлов.

КОМАНДНАЯ СТРОКА
----------------
Один файл:
    py -3 im_to_obj_universal.py "model.im"

Несколько файлов:
    py -3 im_to_obj_universal.py "body.im" "glass.im" "shadow.im"

Вся папка рекурсивно:
    py -3 im_to_obj_universal.py "C:\TrainzAsset"

Можно передать config.txt:
    py -3 im_to_obj_universal.py "C:\TrainzAsset\config.txt"

Экспорт в отдельную папку с сохранением подпапок:
    py -3 im_to_obj_universal.py "C:\TrainzAsset" -o "C:\OBJ_EXPORT"

Копировать найденные текстуры:
    py -3 im_to_obj_universal.py "C:\TrainzAsset" -o "C:\OBJ_EXPORT" --copy-textures

Преобразовать Z-up в Y-up:
    py -3 im_to_obj_universal.py "model.im" --y-up

Обратный порядок полигонов:
    py -3 im_to_obj_universal.py "model.im" --reverse-winding

Изменить масштаб:
    py -3 im_to_obj_universal.py "model.im" --scale 0.01

Второй UV-набор:
    py -3 im_to_obj_universal.py "model.im" --uv-set 1

Добавить vertex RGB в строки v для GEOM 104:
    py -3 im_to_obj_universal.py "model.im" --vertex-colors

Строгий режим без частичного спасения:
    py -3 im_to_obj_universal.py "model.im" --strict

Отключить применение rigid bone transforms:
    py -3 im_to_obj_universal.py "model.im" --no-apply-bones

ОГРАНИЧЕНИЯ
-----------
• «.im» — не уникальное расширение. Конвертер работает именно с Trainz/Auran
  Indexed Mesh JIRF/IDXM, а не с любым форматом, который случайно называется IM.
• OBJ не поддерживает анимацию, skinning, игровые шейдеры Trainz и .kin.
  В OBJ сохраняется статическая bind/rest pose геометрия.
• Несколько UV-наборов одновременно OBJ штатно не хранит — выбирается один.
• Бинарный *.texture не является обычной картинкой. Для автоматического
  подключения желательно иметь *.texture.txt и указанное в нём изображение.
• Зашифрованные, намеренно защищённые или сильно повреждённые модели могут
  экспортироваться частично либо не экспортироваться. В этом случае приложите
  проблемный .im и созданный *_report.txt: по ним можно добавить новый layout.
• Конвертация не меняет лицензию исходного ассета.
