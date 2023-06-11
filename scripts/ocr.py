import os
import subprocess
import xml.etree.ElementTree as ET


def ocr_pgs_subtitles(subtitle_files, languages):
    print(f"[OCR] Performing OCR on PGS subtitles (this may take a while)...")
    output_subtitles = []
    generated_srt_files = []
    replaced_index = 0
    updated_subtitle_languages = languages

    for index, file in enumerate(subtitle_files):
        base, _, extension = file.rpartition('.')
        env = os.environ.copy()
        env['TESSDATA_PREFIX'] = os.path.expanduser('~/.mkv-auto/tessdata')

        command = ["pgsrip", "--language", languages[index + replaced_index], file]

        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise Exception("Error executing pgsrip command: " + result.stdout)

        output_subtitles.append(f"{base}.srt")
        generated_srt_files.append('srt')
        updated_subtitle_languages.insert(replaced_index, languages[index + replaced_index])
        replaced_index += 1

    return output_subtitles, updated_subtitle_languages, generated_srt_files


def ocr_vobsub_subtitles(subtitle_files, languages):
    print(f"[OCR] Performing OCR on VobSub subtitles (this may take a while)...")

    tessdata_location = '~/.mkv-auto/'
    subtitleedit = 'utilities/SubtitleEdit/SubtitleEdit.exe'
    output_subtitles = []
    generated_srt_files = []
    replaced_index = 0
    updated_subtitle_languages = languages

    for index, file in enumerate(subtitle_files):
        base, _, extension = file.rpartition('.')
        env = os.environ.copy()
        env['TESSDATA_PREFIX'] = os.path.expanduser(tessdata_location)

        update_tesseract_lang_xml(languages[index + replaced_index])
        command = ["mono", subtitleedit, "/convert", file,
                   "srt", "/FixCommonErrors", "/encoding:utf-8"]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Error executing SubtitleEdit command: " + result.stderr)

        output_subtitles.append(f"{base}.srt")
        generated_srt_files.append('srt')
        updated_subtitle_languages.insert(replaced_index, languages[index + replaced_index])
        replaced_index += 1

    return output_subtitles, updated_subtitle_languages, generated_srt_files


def update_tesseract_lang_xml(new_language):
    se_settings = 'utilities/SubtitleEdit/Settings.xml'
    # Parse XML file
    tree = ET.parse(se_settings)
    root = tree.getroot()

    for parent1 in root.findall('VobSubOcr'):
        target_elem = parent1.find('TesseractLastLanguage')
        if target_elem is not None:
            target_elem.text = new_language

    # Write back to file
    tree.write(se_settings)

