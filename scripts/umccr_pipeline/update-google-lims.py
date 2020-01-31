import os
import sys
import argparse
import re
import csv
from glob import glob
from datetime import datetime
from sample_sheet import SampleSheet
import logging
from logging.handlers import RotatingFileHandler
import gspread  # maybe move to https://github.com/aiguofer/gspread-pandas
from gspread_pandas import Spread
from oauth2client.service_account import ServiceAccountCredentials

import warnings
warnings.simplefilter("ignore")

################################################################################
# CONSTANTS

SHEET_NAME_RUNS = 'Sheet1'
SHEET_NAME_FAILED = 'Failed Runs'
DEPLOY_ENV = os.getenv('DEPLOY_ENV')
if not DEPLOY_ENV:
    raise ValueError("DEPLOY_ENV needs to be set!")
SCRIPT = os.path.basename(__file__)
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

# The column names of the Google LIMS (in order!)
# Names and values should be kept in sync between the lab internal library tracking sheet and the Google LIMS
illumina_id_column_name = 'IlluminaID'
run_column_name = 'Run'
timestamp_column_name = 'Timestamp'
subject_id_column_name = 'SubjectID'  # the internal ID for the subject/patient
sample_id_column_name = 'SampleID'  # the internal ID for the sample
library_id_column_name = 'LibraryID'  # the internal ID for the library
subject_ext_id_column_name = 'ExternalSubjectID'  # the external (provided) ID for the subject/patient
sample_ext_id_column_name = 'ExternalSampleID'  # is the external (provided) sample ID
library_ext_id_column_name = 'ExternalLibraryID'  # is the external (provided) library ID
sample_name_column_name = 'SampleName'  # the sample name assigned by the lab
project_owner_column_name = 'ProjectOwner'
project_name_column_name = 'ProjectName'
type_column_name = 'Type'  # the assay type: WGS, WTS, 10X, ...
assay_column_name = 'Assay'
phenotype_column_name = 'Phenotype'  # tomor, normal, negative-control, ...
source_column_name = 'Source'  # tissue, FFPE, ...
quality_column_name = 'Quality'  # Good, Poor, Borderline
topup_column_name = 'Topup'
secondary_analysis_column_name = 'SecondaryAnalysis'
fastq_column_name = 'FASTQ'
number_fastqs_column_name = 'NumberFASTQS'
results_column_name = 'Results'
trello_column_name = 'Trello'
notes_column_name = 'Notes'
todo_column_name = 'ToDo'
# List of column names expected to be found in the tracking sheet
metadata_column_names = (
    subject_id_column_name,
    subject_ext_id_column_name,
    sample_id_column_name,
    sample_ext_id_column_name,
    sample_name_column_name,
    library_id_column_name,
    type_column_name,
    phenotype_column_name,
    source_column_name,
    assay_column_name,
    project_name_column_name,
    project_owner_column_name,
    quality_column_name)


# column headers of the LIMS spreadsheet
sheet_column_headers = (
    illumina_id_column_name,
    run_column_name,
    timestamp_column_name,
    subject_id_column_name,
    sample_id_column_name,
    library_id_column_name,
    subject_ext_id_column_name,
    sample_ext_id_column_name,
    library_ext_id_column_name,
    sample_name_column_name,
    project_owner_column_name,
    project_name_column_name,
    type_column_name,
    assay_column_name,
    phenotype_column_name,
    source_column_name,
    quality_column_name,
    topup_column_name,
    secondary_analysis_column_name,
    fastq_column_name,
    number_fastqs_column_name,
    results_column_name,
    trello_column_name,
    notes_column_name,
    todo_column_name)

# define argument defaults
if DEPLOY_ENV == 'prod':
    raw_data_base_dir = '/storage/shared/raw/Baymax'
    bcl2fastq_base_dir = '/storage/shared/bcl2fastq_output'
    LOG_FILE_NAME = os.path.join(SCRIPT_DIR, SCRIPT + ".log")
    lims_spreadsheet_id = '1aaTvXrZSdA1ekiLEpW60OeNq2V7D_oEMBzTgC-uDJAM'  # 'Google LIMS' in Team Drive
    lab_spreadsheet_id = '1pZRph8a6-795odibsvhxCqfC6l0hHZzKbGYpesgNXOA'  # Lab metadata tracking sheet (prod)
else:
    raw_data_base_dir = '/storage/shared/dev/Baymax'
    bcl2fastq_base_dir = '/storage/shared/dev/bcl2fastq_output'
    LOG_FILE_NAME = os.path.join(SCRIPT_DIR, SCRIPT + ".dev.log")
    lims_spreadsheet_id = '1vX89Km1D8dm12aTl_552GMVPwOkEHo6sdf1zgI6Rq0g'  # 'Google LIMS dev' in Team Drive
    lab_spreadsheet_id = '1Pgz13btHOJePiImo-NceA8oJKiQBbkWI5D2dLdKpPiY'  # Lab metadata tracking sheet (dev)

# lab_spreadsheet_id = '1pZRph8a6-795odibsvhxCqfC6l0hHZzKbGYpesgNXOA'
runfolder_name_expected_length = 29
fastq_hpc_base_dir = 's3://umccr-fastq-data-prod/'
csv_outdir = '/tmp'
write_csv = False
creds_file = "/home/limsadmin/.google/google-lims-updater-b50921f70155.json"
skip_lims_update = False
failed_run = False

# pre-compile regex patterns
runfolder_pattern = re.compile('(\d{6})_.+_(\d{4})_.+')


################################################################################
# METHODS

def getLogger():
    new_logger = logging.getLogger(__name__)
    new_logger.setLevel(logging.DEBUG)

    # create a logging format
    formatter = logging.Formatter('%(asctime)s - %(module)s - %(name)s - %(levelname)s : %(lineno)d - %(message)s')

    # create a file handler
    file_handler = RotatingFileHandler(filename=LOG_FILE_NAME, maxBytes=100000000, backupCount=5)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # create a console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # add the handlers to the logger
    new_logger.addHandler(file_handler)
    new_logger.addHandler(console_handler)

    return new_logger


def get_year_from_lib_id(library_id):
    # TODO: check library ID format and make sure we have proper years
    if library_id.startswith('LPRJ'):
        return '20' + library_id[4:6]
    else:
        return '20' + library_id[1:3]


def get_library_sheet_from_google(year):
    logger.info(f"Loading tracking data for year {year}")
    spread = Spread(lab_spreadsheet_id)
    library_tracking_spreadsheet_df = spread.sheet_to_df(sheet=year, index=0, header_rows=1, start_row=1)
    hit = library_tracking_spreadsheet_df.iloc[0]
    logger.debug(f"First record: {hit}")
    for column_name in metadata_column_names:
        logger.debug(f"Checking for column name {column_name}...")
        if column_name not in hit:
            logger.error(f"Could not find column {column_name}. The file is not structured as expected! Aborting.")
            exit(-1)
    logger.info(f"Loaded {len(library_tracking_spreadsheet_df.index)} records from library tracking sheet.")
    return library_tracking_spreadsheet_df


def get_meta_data_by_library_id(library_id):
    year = get_year_from_lib_id(library_id)
    library_tracking_spreadsheet_df = library_tracking_spreadsheet.get(year)
    try:
        hit = library_tracking_spreadsheet_df[library_tracking_spreadsheet_df[library_id_column_name] == library_id]
    except (KeyError, NameError, TypeError, Exception) as er:
        logger.error(f"Error trying to find library ID {library_id}! Error: {er}")

    # We expect exactly one matching record, not more, not less!
    if len(hit) == 1:
        logger.debug(f"Unique entry found for sample ID {library_id}")
    elif len(hit) > 1:
        logger.error(f"Multiple entries for library ID {library_id}!")
    else:
        logger.error(f"No entry for library ID {library_id}")
    return hit


def write_csv_file(output_file, column_headers, data_rows):
    with open(output_file, 'w', newline='') as csvfile:
        sheetwriter = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
        sheetwriter.writerow(column_headers)
        for row in data_rows:
            sheetwriter.writerow(row)


def next_available_row(worksheet):
    str_list = list(filter(None, worksheet.col_values(1)))  # fastest
    return len(str_list)+1


def write_to_google_lims(keyfile, lims_spreadsheet_id, data_rows, failed_run):
    # follow example from:
    # https://www.twilio.com/blog/2017/02/an-easy-way-to-read-and-write-to-a-google-spreadsheet-in-python.html
    scope = ['https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(keyfile, scope)
    client = gspread.authorize(creds)

    params = {
        'valueInputOption': 'USER_ENTERED'
    }
    body = {
        'majorDimension': 'ROWS',
        'values': list(data_rows)
    }

    spreadsheet = client.open_by_key(lims_spreadsheet_id)
    if failed_run:
        spreadsheet.values_append(SHEET_NAME_FAILED, params, body)
    else:
        spreadsheet.values_append(SHEET_NAME_RUNS, params, body)


def split_at(s, c, n):
    words = s.split(c)
    return c.join(words[:n]), c.join(words[n:])


if __name__ == "__main__":
    logger = getLogger()
    logger.info(f"Invocation with parameters: {sys.argv[1:]}")

    ################################################################################
    # argument parsing

    logger.debug("Setting up argument parser.")
    parser = argparse.ArgumentParser(description='Generate data for LIMS spreadsheet.')
    parser.add_argument('runfolder',
                        help="The run/runfolder name.")
    parser.add_argument('--raw-data-base-dir',
                        help="The path to raw data (where to find the sample sheet used).",
                        default=raw_data_base_dir)
    parser.add_argument('--bcl2fastq-base-dir',
                        help="The base path (where to find the bcl2fastq output).",
                        default=bcl2fastq_base_dir)
    parser.add_argument('--fastq-hpc-base-dir',
                        help="The destination base path (depends on HPC env. ",
                        default=fastq_hpc_base_dir)
    parser.add_argument('--csv-outdir',
                        help="Where to write the CSV output file to. Default=/tmp).",
                        default=csv_outdir)
    parser.add_argument('--write-csv', action='store_true',
                        help="Use this flag to write a CSV file.")
    parser.add_argument('--lims-spreadsheet-id',
                        help="The name of the Google LIMS spreadsheet.",
                        default=lims_spreadsheet_id)
    parser.add_argument('--creds-file',
                        help="The Google credentials file to grant access to the spreadsheet.",
                        default=creds_file)
    parser.add_argument('--skip-lims-update', action='store_true',
                        help="Use this flag to skip the update of the Google LIMS.")
    parser.add_argument('--failed-run', action='store_true',
                        help="Use this flag to indicate a failed run (updates the Failed Runs sheet).")

    logger.debug("Parsing arguments.")
    args = parser.parse_args()
    if args.raw_data_base_dir:
        raw_data_base_dir = args.raw_data_base_dir
    if args.bcl2fastq_base_dir:
        bcl2fastq_base_dir = args.bcl2fastq_base_dir
    if args.fastq_hpc_base_dir:
        fastq_hpc_base_dir = args.fastq_hpc_base_dir
    if args.csv_outdir:
        csv_outdir = args.csv_outdir
    if args.write_csv:
        write_csv = True
    if args.lims_spreadsheet_id:
        lims_spreadsheet_id = args.lims_spreadsheet_id
    if args.creds_file:
        creds_file = args.creds_file
    if args.skip_lims_update:
        skip_lims_update = True
    if args.failed_run:
        failed_run = True
    runfolder = args.runfolder

    # extract date and run number from runfolder name
    logger.debug("Parsing runfolder name.")
    if len(runfolder) != runfolder_name_expected_length:
        raise ValueError(f"Runfolder name {runfolder} did not match the expected \
                          length of {runfolder_name_expected_length} characters!")
    # TODO: perhaps include other runfolder name syntax checks
    try:
        run_date = re.search(runfolder_pattern, runfolder).group(1)
        run_no = re.search(runfolder_pattern, runfolder).group(2)
    except AttributeError:
        raise ValueError(f"Runfolder name {runfolder} did not match expected format: {runfolder_pattern}")

    run_timestamp = datetime.strptime(run_date, '%y%m%d').strftime('%Y-%m-%d')
    run_year = datetime.strptime(run_date, '%y%m%d').strftime('%Y')
    run_number = int(run_no)
    logger.info(f"Extracted run number/year/timestamp: {run_number}/{run_year}/{run_timestamp}")

    # load the library tracking sheet for the run year
    logger.debug("Loading library tracking data.")
    # global variables
    # TODO: should be refactored in proper class variables
    library_tracking_spreadsheet = dict()  # dict of sheets as dataframes
    for year in ('2019', '2020'):  # TODO: this could be determined scanning though all SampleSheets
        library_tracking_spreadsheet[year] = get_library_sheet_from_google(year)

    ################################################################################
    # Generate LIMS records from SampleSheet

    lims_data_rows = set()

    if failed_run:
        logger.info("Processing failed run. Using original sample sheet.")
        samplesheet_path_pattern = os.path.join(raw_data_base_dir, runfolder, 'SampleSheet.csv')
    else:
        logger.info("Processing successful run. Using generated sample sheet(s).")
        samplesheet_path_pattern = os.path.join(raw_data_base_dir, runfolder, 'SampleSheet.csv.custom.*')

    samplesheet_paths = glob(samplesheet_path_pattern)
    if len(samplesheet_paths) < 1:
        raise ValueError("No sample sheets found!")
    logger.info(f"Using {len(samplesheet_paths)} sample sheet(s).")

    for samplesheet in samplesheet_paths:
        logger.info(f"Processing samplesheet {samplesheet}")
        name, extension = os.path.splitext(samplesheet)
        samples = SampleSheet(samplesheet).samples
        logger.info(f"Found {len(samples)} samples.")
        for sample in samples:
            logger.debug(f"Looking up metadata with {sample.Sample_Name} for samplesheet.Sample_ID (UMCCR SampleID); " +
                         f"{sample.Sample_ID} and samplesheet.sample_Name (UMCCR LibraryID): {sample.Sample_Name}")
            column_values = get_meta_data_by_library_id(sample.Sample_Name)

            fastq_pattern = os.path.join(bcl2fastq_base_dir, runfolder, sample.Sample_Project,
                                         sample.Sample_ID, sample.Sample_Name + "*.fastq.gz")
            s3_fastq_pattern = os.path.join(fastq_hpc_base_dir, runfolder, sample.Sample_Project,
                                            sample.Sample_ID, sample.Sample_Name + "*.fastq.gz")

            logger.debug('Looking for FASTQs: ' + fastq_pattern)
            fastq_file_paths = glob(fastq_pattern)
            if len(fastq_file_paths) < 1:
                logger.warn(f"Found no FASTQ files for sample {sample.Sample_ID}!")

            # splitting the combined sample name
            if sample.Sample_ID.startswith('NTC') or sample.Sample_ID.startswith('PTC'):
                s_id, es_id = split_at(sample.Sample_ID, '_', 2)
            else:
                s_id, es_id = split_at(sample.Sample_ID, '_', 1)
            print(f"Split SampleID {sample.Sample_ID} into intID {s_id} and extID {es_id}")
            print(f"Inserting values {column_values}")
            # double check LibraryID
            if not sample.Sample_Name == column_values[library_id_column_name].item():
                raise ValueError(f"Library IDs did not match. Samplesheet: {sample.Sample_Name} " +
                                 f"Tracking sheet: {column_values[library_id_column_name].item()}")
            lims_data_rows.add((runfolder,
                                run_number,
                                run_timestamp,
                                column_values[subject_id_column_name].item(),
                                column_values[sample_id_column_name].item(),
                                column_values[library_id_column_name].item(),
                                column_values[subject_ext_id_column_name].item(),
                                column_values[sample_ext_id_column_name].item(),
                                '-',
                                column_values[sample_name_column_name].item(),
                                column_values[project_owner_column_name].item(),
                                column_values[project_name_column_name].item(),
                                column_values[type_column_name].item(),
                                column_values[assay_column_name].item(),
                                column_values[phenotype_column_name].item(),
                                column_values[source_column_name].item(),
                                column_values[quality_column_name].item(),
                                '-',
                                '-',
                                s3_fastq_pattern,
                                len(fastq_file_paths),
                                '-',
                                '-',
                                '-',
                                '-'))

    ################################################################################
    # write the data into a CSV file

    if write_csv:
        output_file = os.path.join(csv_outdir, runfolder + '-lims-sheet.csv')
        logger.info(f"Writing {len(lims_data_rows)} records to CSV file {output_file}")
        write_csv_file(output_file=output_file, column_headers=sheet_column_headers, data_rows=lims_data_rows)
    else:
        logger.info("Not writing CSV file.")

    if skip_lims_update:
        logger.warn("Skipping Google LIMS update!")
    else:
        logger.info(f"Writing {len(lims_data_rows)} records to Google LIMS {lims_spreadsheet_id}")
        write_to_google_lims(keyfile=creds_file, lims_spreadsheet_id=lims_spreadsheet_id,
                             data_rows=lims_data_rows, failed_run=failed_run)

    logger.info("All done.")
