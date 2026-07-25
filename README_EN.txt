UNIVERSAL TRAINZ / AURAN .IM → WAVEFRONT .OBJ
Version 2.1
==================================================

This converter is designed for the Trainz/Auran Indexed Mesh family using
the JIRF/IDXM structure. It is not limited to one specific model.

QUICK START
-----------
1. Install Python 3.10 or newer from python.org.
   During installation, enable "Add Python to PATH".
2. Keep the BAT files and im_to_obj_universal.py in the same folder.
3. Drag and drop any of the following onto:
   DRAG_AND_DROP_IM_CONFIG_OR_FOLDER.bat

   • one or more .im files;
   • a config.txt file;
   • an entire Trainz asset folder.

4. Folders are scanned recursively. A failed file does not stop the rest
   of the batch.

You can also place the entire converter package in the root of a Trainz
asset and run:

   CONVERT_ALL_IM_IN_THIS_FOLDER.bat

The results will be written to OBJ_EXPORT while preserving the original
subfolder structure.

SUPPORTED FEATURES
------------------
• standard JIRF/IDXM wrapper;
• third-party raw IDXM files without JIRF;
• INFO 100 and newer;
• MATL 100/101 and 102/103, including properties and opacity;
• normal and wide JET strings;
• GEOM 100, 101, 102, 103, 104, 200 and 201;
• compatible versions newer than 201 that use the same base layout;
• GEOM 104 both with and without vertex colors;
• GEOM 201 both with and without tangent data;
• triangles, lines and points;
• standard 16-bit indices and heuristic fallback for 32-bit indices;
• multiple UV sets — one selected set is exported to OBJ, set 0 by default;
• attachment points exported to *_attachments.csv;
• SKEL/INFL bone hierarchy;
• reconstruction of rigid parent-bone transforms for static OBJ export;
• batch conversion that continues after a damaged file;
• partial recovery of readable CHNK sections from damaged IM files;
• lookup of texture.txt and source TGA/PNG/JPG/BMP/DDS/WEBP/TIFF images;
• copying of found images into a textures subfolder when launched via BAT.

OUTPUT FILES
------------
For each model, the converter creates:

• <name>.obj — geometry;
• <name>.mtl — materials and image references;
• <name>_attachments.csv — attachment points, when present;
• <name>_bones.csv — bones, when present;
• <name>_report.txt — block versions, statistics and warnings.

For the entire batch, it creates:

• _im_to_obj_batch_report.txt — list of successful, skipped and failed files.

COMMAND LINE
------------
Convert one file:

    py -3 im_to_obj_universal.py "model.im"

Convert several files:

    py -3 im_to_obj_universal.py "body.im" "glass.im" "shadow.im"

Recursively convert an entire folder:

    py -3 im_to_obj_universal.py "C:\TrainzAsset"

You can pass config.txt directly:

    py -3 im_to_obj_universal.py "C:\TrainzAsset\config.txt"

Export to a separate folder while preserving subfolders:

    py -3 im_to_obj_universal.py "C:\TrainzAsset" -o "C:\OBJ_EXPORT"

Copy discovered textures:

    py -3 im_to_obj_universal.py "C:\TrainzAsset" -o "C:\OBJ_EXPORT" --copy-textures

Convert Z-up coordinates to Y-up:

    py -3 im_to_obj_universal.py "model.im" --y-up

Reverse polygon winding:

    py -3 im_to_obj_universal.py "model.im" --reverse-winding

Change model scale:

    py -3 im_to_obj_universal.py "model.im" --scale 0.01

Export the second UV set:

    py -3 im_to_obj_universal.py "model.im" --uv-set 1

Write vertex RGB values into OBJ vertex lines for GEOM 104:

    py -3 im_to_obj_universal.py "model.im" --vertex-colors

Use strict mode without partial recovery:

    py -3 im_to_obj_universal.py "model.im" --strict

Disable application of rigid bone transforms:

    py -3 im_to_obj_universal.py "model.im" --no-apply-bones

LIMITATIONS
-----------
• ".im" is not a unique extension. This converter specifically supports
  Trainz/Auran Indexed Mesh JIRF/IDXM files, not every unrelated format that
  happens to use the .im extension.

• OBJ does not support animation, skinning, Trainz game shaders or .kin files.
  The exported OBJ contains static geometry in its bind/rest pose.

• Standard OBJ does not store several UV sets at the same time. One UV set
  must be selected for export.

• A binary *.texture file is not a normal image file. Automatic material
  setup works best when the matching *.texture.txt file and referenced source
  image are available.

• Encrypted, intentionally protected or heavily damaged models may be
  exported only partially or may fail to export. In that case, provide the
  problematic .im file together with the generated *_report.txt file so that
  support for the unknown layout can be added.

• Conversion does not change the license of the original asset. You must
  follow the original author's license when using the exported files.

VERSION 2.1 VALIDATION
----------------------
Real IS20-16A.im test model:

• 20 objects/material sections;
• 20,661 vertices;
• 19,123 triangles;
• 19 attachment points;
• the exported OBJ was successfully reloaded with an independent mesh loader.

Additional synthetic test files covered:

• GEOM 100, 101, 102, 103, 104, 200, 201 and 202-compatible layouts;
• GEOM 104 with and without vertex colors;
• GEOM 201 with and without tangents;
• MATL 100, 101, 102 and 103;
• wide strings;
• raw IDXM;
• line primitives;
• rigid parent bones;
• a damaged extra CHNK section in salvage mode.

WINDOWS FILENAME COMPATIBILITY
------------------------------
All executable filenames in this package use ASCII characters only. This
prevents corrupted Cyrillic filenames in Windows Explorer and Command Prompt
when the system locale or ZIP extractor uses a different code page.
