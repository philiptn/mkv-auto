import sys
import argparse
from configparser import ConfigParser
from itertools import groupby

from scripts.file_operations import *
from scripts.mkv import *
from scripts.ocr import *
from scripts.srt import *

# Get user preferences
variables = ConfigParser()

# If user-specific config file has been created, load it
# else, load defaults from preferences.ini
if os.path.isfile('user.ini'):
	variables.read('user.ini')
else:
	variables.read('defaults.ini')

# General
temp_dir = variables.get('general', 'TEMP_DIR')
file_tag = variables.get('general', 'FILE_TAG')
flatten_directories = True if variables.get('general', 'FLATTEN_DIRECTORIES').lower() == "true" else False
remove_samples = True if variables.get('general', 'REMOVE_SAMPLES').lower() == "true" else False
movies_folder = variables.get('general', 'MOVIES_FOLDER')
movies_hdr_folder = variables.get('general', 'MOVIES_HDR_FOLDER')
tv_shows_folder = variables.get('general', 'TV_SHOWS_FOLDER')
tv_shows_hdr_folder = variables.get('general', 'TV_SHOWS_HDR_FOLDER')
others_folder = variables.get('general', 'OTHERS_FOLDER')

# Audio
pref_audio_langs = [item.strip() for item in variables.get('audio', 'PREFERRED_AUDIO_LANG').split(',')]
remove_commentary = True if variables.get('audio', 'REMOVE_COMMENTARY_TRACK').lower() == "true" else False

# Subtitles
pref_subs_langs = [item.strip() for item in variables.get('subtitles', 'PREFERRED_SUBS_LANG').split(',')]
pref_subs_langs_short = [item.strip()[:-1] for item in variables.get('subtitles', 'PREFERRED_SUBS_LANG').split(',')]
always_enable_subs = True if variables.get('subtitles', 'ALWAYS_ENABLE_SUBS').lower() == "true" else False
always_remove_sdh = True if variables.get('subtitles', 'REMOVE_SDH').lower() == "true" else False
remove_music = True if variables.get('subtitles', 'REMOVE_MUSIC').lower() == "true" else False
resync_subtitles = variables.get('subtitles', 'RESYNC_SUBTITLES').lower()


def mkv_auto(args):

	# Defaults
	input_dir = 'input/'
	output_dir = 'output/'

	if args.input_dir:
		input_dir = args.input_dir

	if args.output_dir:
		output_dir = args.output_dir

	if os.path.exists(temp_dir):
		shutil.rmtree(temp_dir)
	
	os.mkdir(temp_dir)

	total_files = count_files(input_dir)
	total_bytes = count_bytes(input_dir)

	#print('')
	if not args.silent:
		# Hide the cursor
		sys.stdout.write('\033[?25l')
		sys.stdout.flush()

	with tqdm(total=total_bytes, unit='B', unit_scale=True, unit_divisor=1024,
			  bar_format='\r{desc}{bar:10} {percentage:3.0f}%', leave=False, disable=args.silent) as pbar:
		pbar.set_description(f"[INFO] Copying file 1 of {total_files}")
		copy_directory_contents(input_dir, temp_dir, pbar, total_files=total_files)

	input_dir = temp_dir

	convert_all_videos_to_mkv(input_dir, args.silent)
	rename_others_file_to_folder(input_dir, movies_folder, tv_shows_folder, movies_hdr_folder, tv_shows_hdr_folder, others_folder)

	if not args.silent:
		# Show the cursor
		sys.stdout.write('\033[?25h')
		sys.stdout.flush()

	extract_archives(input_dir)

	if remove_samples:
		remove_sample_files_and_dirs(input_dir)

	if flatten_directories:
		flatten_dirs(input_dir)

	fix_episodes_naming(input_dir)
	remove_ds_store(input_dir)

	total_files = get_total_mkv_files(input_dir)
	file_index = 1

	if total_files == 0 and not args.input_file:
		shutil.rmtree(temp_dir, ignore_errors=True)

		if not args.silent:
			# Show the cursor
			sys.stdout.write('\033[?25h')
			sys.stdout.flush()
		print(f"[ERROR] No mkv files found in input directory.\n")
		exit(0)

	errored_file_names = []
	dirpaths = []

	for dirpath, dirnames, filenames in os.walk(input_dir):
		dirnames.sort(key=str.lower)  # sort directories in-place in case-insensitive manner

		# Skip directories or files starting with '.'
		if '/.' in dirpath or dirpath.startswith('./.'):
			continue

		if not dirpath == 'input/':
			dirpaths.append(dirpath)

		structure = os.path.join(output_dir, os.path.relpath(dirpath, input_dir))

		input_file_mkv = ''
		input_file_mkv_nopath = ''
		input_file = ''
		output_file = ''
		output_file_mkv = ''
		mkv_dirpath = ''
		file_names = []
		file_name_printed = False
		external_subs_print = True
		quiet = False

		def split_filename(filename):
			match = re.match(r'^(.*?\d+)\.(.*)(\.\w{2,3})$', filename)
			if match:
				base_name, rest, extension = match.groups()
				lang_code = rest.split('.')[-1] if extension == '.srt' else ''
				return (base_name, extension_priority(extension), lang_code, rest)
			else:
				return (filename, 3, '', '')

		def extension_priority(extension):
			if extension == ".srt":
				return 0
			elif extension == ".mkv":
				return 2
			else:
				return 1

		# Ignore files that start with a dot
		filenames = [f for f in filenames if not f.startswith('.')]

		# Sort filenames using the custom sort function
		filenames.sort(key=split_filename)

		# Group the filenames by base_name
		for base_name, group in groupby(filenames, key=lambda x: split_filename(x)[0]):
			grouped_files = list(group)
			# Within each group, sort the files first by extension priority and then by language code
			grouped_files.sort(key=lambda x: (split_filename(x)[1], split_filename(x)[2]))

		mkv_file_found = False
		for index, file_name in enumerate(filenames):
			try:
				if file_name.startswith('.'):
					continue

				input_file = os.path.join(dirpath, file_name)
				output_file = os.path.join(structure, file_name)

				needs_tag_rename = True

				parts = file_name.split('.') 
				language_prefix = parts[-2] # The language prefix is always the second to last part

				if file_name.endswith('.srt'):
					if language_prefix in pref_subs_langs_short or language_prefix in pref_subs_langs:
						if not mkv_file_found:
							last_processed_mkv = ''
							try:
								with open(".last_processed_mkv.txt", "r") as f:
									last_processed_mkv = f.read().strip()
							except FileNotFoundError:
								pass

							mkv_files = [file for file in filenames if file.endswith('.mkv') and file != last_processed_mkv]
							if mkv_files:
								file_name = mkv_files[0]
								input_file_mkv = os.path.join(dirpath, str(file_name))
								input_file_mkv_nopath = str(file_name)
								with open(".last_processed_mkv.txt", "w") as f:
									f.write(file_name)
								mkv_file_found = True

						if not file_name_printed:
							print(f"[INFO] Processing file {file_index} of {total_files}:\n")
							print(f"[FILE] '{input_file_mkv_nopath}'")
							file_name_printed = True
						if external_subs_print:
							quiet = True
						input_files = [input_file]
						if always_remove_sdh or remove_music:
							if external_subs_print:
								print("[SRT_EXT] Removing SDH in external subtitles...")
							remove_sdh(input_files, quiet, remove_music)
						if resync_subtitles == 'fast':
							if external_subs_print:
								print("[SRT_EXT] Synchronizing external subtitles to audio track (fast)...")
							resync_srt_subs_fast(input_file_mkv, input_files, quiet)
						elif resync_subtitles == 'ai':
							if external_subs_print:
								print("[SRT_EXT] Synchronizing external subtitles to audio track (ai)...")
							resync_srt_subs_ai(input_file_mkv, input_files, quiet)
						external_subs_print = False

						if needs_tag_rename:
							if file_tag != "default":
								updated_filename = replace_tags_in_file(input_file, file_tag)
								file_name = updated_filename

								input_file = os.path.join(dirpath, file_name)
								output_file = os.path.join(structure, file_name)

						move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder,
							movies_hdr_folder, tv_shows_hdr_folder, others_folder)
						continue
					else:
						os.remove(input_file)
						continue

				elif file_name.endswith('.mkv'):
					mkv_file_found = False
					if not file_name_printed:
						print(f"[INFO] Processing file {file_index} of {total_files}:\n")
						print(f"[FILE] '{file_name}'")
						file_name_printed = True

					external_subs_print = True
					quiet = False
					output_file_mkv = output_file

					# Get file info using mkvinfo
					file_info, pretty_file_info = get_mkv_info(input_file)

					wanted_audio_tracks, \
						default_audio_track, needs_processing_audio = get_wanted_audio_tracks(file_info, pref_audio_langs, remove_commentary)
					wanted_subs_tracks, default_subs_track, \
						needs_sdh_removal, needs_convert, a, b, needs_processing_subs = get_wanted_subtitle_tracks(file_info, pref_subs_langs)
					print_track_audio_str = 'tracks' if len(wanted_audio_tracks) != 1 else 'track'
					print_track_subs_str = 'tracks' if len(wanted_subs_tracks) != 1 else 'track'

					if needs_processing_audio or needs_processing_subs or needs_sdh_removal or needs_convert:
						strip_tracks_in_mkv(input_file, wanted_audio_tracks, default_audio_track,
											wanted_subs_tracks, default_subs_track, always_enable_subs)
					else:
						print(f"[MKVMERGE] No track filtering needed.")
						needs_tag_rename = False

					if needs_processing_subs:
						subtitle_files = []
						# Get updated file info after mkv tracks reduction
						file_info, pretty_file_info = get_mkv_info(input_file)
						wanted_subs_tracks, a, b, needs_convert, \
							sub_filetypes, subs_track_languages, e = get_wanted_subtitle_tracks(file_info, pref_subs_langs)

						updated_subtitle_languages = subs_track_languages

						# Check if any of the subtitle tracks needs to be converted using OCR
						if needs_convert:
							print(f"[MKVEXTRACT] Some subtitles need to be converted to SRT, extracting subtitles...")
							output_subtitles = []
							generated_srt_files = []

							if "sub" in sub_filetypes:
								subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
																	  sub_filetypes, subs_track_languages)

								# If there is a mix of srt files alongside (different languages), then
								# the srt file will be removed after it has been extracted
								alongside_srt_langs = []
								alongside_srt_files = []
								for index, filetype in enumerate(sub_filetypes):
									if filetype == "srt":
										alongside_srt_langs.append(subs_track_languages[index])
										alongside_srt_files.append("srt")
										sub_filetypes.pop(index)
										subtitle_files.pop(index)
										subs_track_languages.pop(index)

								output_subtitles, updated_subtitle_languages, generated_srt_files = ocr_vobsub_subtitles(subtitle_files, subs_track_languages)

								for file in alongside_srt_files:
									sub_filetypes.insert(0, file)
								for lang in alongside_srt_langs:
									updated_subtitle_languages.insert(0, lang)

							elif "sup" in sub_filetypes:
								subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
																	  sub_filetypes, subs_track_languages)

								# If there is a mix of srt files alongside (different languages), then
								# the srt file will be removed after it has been extracted
								alongside_srt_langs = []
								alongside_srt_files = []
								for index, filetype in enumerate(sub_filetypes):
									if filetype == "srt":
										alongside_srt_langs.append(subs_track_languages[index])
										alongside_srt_files.append("srt")
										sub_filetypes.pop(index)
										subtitle_files.pop(index)
										subs_track_languages.pop(index)

								output_subtitles, updated_subtitle_languages, generated_srt_files = ocr_pgs_subtitles(subtitle_files, subs_track_languages)
								
								for file in alongside_srt_files:
									sub_filetypes.insert(0, file)
								for lang in alongside_srt_langs:
									updated_subtitle_languages.insert(0, lang)

							elif "ass" in sub_filetypes:
								subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
																	  sub_filetypes, subs_track_languages)

								# If there is a mix of srt files alongside (different languages), then
								# the srt file will be removed after it has been extracted
								alongside_srt_langs = []
								alongside_srt_files = []
								for index, filetype in enumerate(sub_filetypes):
									if filetype == "srt":
										alongside_srt_langs.append(subs_track_languages[index])
										alongside_srt_files.append("srt")
										sub_filetypes.pop(index)
										subtitle_files.pop(index)
										subs_track_languages.pop(index)

								output_subtitles, updated_subtitle_languages, generated_srt_files = convert_ass_to_srt(subtitle_files, subs_track_languages)

								for file in alongside_srt_files:
									sub_filetypes.insert(0, file)
								for lang in alongside_srt_langs:
									updated_subtitle_languages.insert(0, lang)

							if always_remove_sdh:
								remove_sdh(output_subtitles, quiet, remove_music)
								needs_sdh_removal = False

							if resync_subtitles == 'fast':
								resync_srt_subs_fast(input_file, output_subtitles, quiet)
							elif resync_subtitles == 'ai':
								resync_srt_subs_ai(input_file, output_subtitles, quiet)

							for file in generated_srt_files:
								sub_filetypes.insert(0, file)

							remove_cc_hidden_in_file(input_file)
							repack_tracks_in_mkv(input_file, sub_filetypes, updated_subtitle_languages, pref_subs_langs)

						elif not needs_convert:
							if needs_sdh_removal and always_remove_sdh or resync_subtitles != 'false':
								subtitle_files = extract_subs_in_mkv(input_file, wanted_subs_tracks,
																 sub_filetypes, subs_track_languages)

							if needs_sdh_removal and (always_remove_sdh or remove_music):
								remove_sdh(subtitle_files, quiet, remove_music)

							if resync_subtitles != 'false':
								if resync_subtitles == 'fast':
									resync_srt_subs_fast(input_file, subtitle_files, quiet)
								elif resync_subtitles == 'ai':
									resync_srt_subs_ai(input_file, subtitle_files, quiet)

							if needs_sdh_removal and always_remove_sdh or resync_subtitles != 'false':
								remove_cc_hidden_in_file(input_file)
								repack_tracks_in_mkv(input_file, sub_filetypes, updated_subtitle_languages, pref_subs_langs)

					if needs_processing_subs:
						remove_all_mkv_track_tags(input_file)

					if needs_tag_rename:
						if file_tag != "default":
							updated_filename = replace_tags_in_file(input_file, file_tag)
							file_name = updated_filename

							input_file = os.path.join(dirpath, file_name)
							output_file = os.path.join(structure, file_name)

					print("[INFO] Moving file to destination folder...")
					move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder,
							movies_hdr_folder, tv_shows_hdr_folder, others_folder)
					file_index += 1
					file_name_printed = False
					print('')
				else:
					continue
			except Exception as e:
				# If some of the functions were to fail, move the file unprocessed instead
				if not args.silent:
					# Show the cursor
					sys.stdout.write('\033[?25h')
					sys.stdout.flush()
				print(f"[ERROR] An unknown error occured. Skipping processing...\n---\n{e}\n---\n")
				errored_file_names.append(file_name)

				move_file_to_output(input_file, output_dir, movies_folder, tv_shows_folder,
							movies_hdr_folder, tv_shows_hdr_folder, others_folder)

				file_index += 1
				file_name_printed = False
				print('')

				continue

	if len(errored_file_names) == 0:
		# Sorting the dirpaths such that entries with
		# the longest subdirectories are removed first
		dirpaths.sort(key=lambda path: path.count('/'), reverse=True)
		for dirpath in dirpaths:
			safe_delete_dir(dirpath)

		try:
			shutil.rmtree(temp_dir, ignore_errors=True)
			os.remove('.last_processed_mkv.txt')
		except:
			pass

		print("[INFO] All files successfully processed.\n")
	else:
		os.remove('.last_processed_mkv.txt')
		print(f"[INFO] During processing {len(errored_file_names)} errors occured in files:")
		for file in errored_file_names:
			print(f"'{file}'")
		print('')
	
	exit(0)


def main():
	# Create the main parser
	parser = argparse.ArgumentParser(description="A tool that aims to remove necessary clutter from Matroska (.mkv) "
												 "files by removing and/or converting any subtitle tracks in the "
												 "source file(s).")
	parser.add_argument("--input_folder", "-if", dest="input_dir", type=str, required=False,
						help="input folder path (default: 'input/')")
	parser.add_argument("--output_folder", "-of", dest="output_dir", type=str, required=False,
						help="output folder path (default: 'output/')")
	parser.add_argument("--silent", action="store_true", default=False, required=False,
					help="supress visual elements like progress bars (default: False)")

	parser.set_defaults(func=mkv_auto)
	args = parser.parse_args()

	# Call the function associated with the active sub-parser
	args.func(args)

	# Run mkv_auto function if no argument is given
	if len(sys.argv) < 2:
		mkv_auto(args)


# Call the main() function if this file is directly executed
if __name__ == '__main__':
	main()
