#!/bin/bash
set -eo pipefail

if test -z "$DEPLOY_ENV"; then
    echo "DEPLOY_ENV is not set! Set it to either 'dev' or 'prod'."
    exit 1
fi
if test "$DEPLOY_ENV" = "dev"; then
  # Wait a bit to simulate work (and avoid tasks running too close to each other)
  sleep 5
fi

script=$(basename $0)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

function write_log {
  msg="$(date +'%Y-%m-%d %H:%M:%S.%N') $script: $1"
  echo "$msg" >> $DIR/${script}.log
  if test "$DEPLOY_ENV" = "prod"; then
    echo "$msg" > /dev/udp/localhost/9999
  else
    echo "$msg"
  fi
}

# Apply regex to truncate the AWS SFN task token, to prevent it from being stored in logs
paramstring=$(echo "$*" | perl -pe  's/(-k|--task-token) ([a-zA-Z0-9]{10})[a-zA-Z0-9]+/$1 $2.../g')
write_log "INFO: Invocation with parameters: $paramstring"

if test "$#" -lt 10; then
  write_log "ERROR: Insufficient parameters"
  echo "Number of provided parameters: $(($# / 2))"
  echo "A minimum of 4 arguments are required!"
  echo "  - The destination bucket [-b|--bucket]"
  echo "  - The destination path [-d|--dest-dir]"
  echo "  - The source path [-s|--source-dir]"
  echo "  - The runfolder name [-n|--runfolder-name]"
  echo "  - An AWS SFN task token [-k|--task-token]"
  echo "  - (optional) The sync exclusions, in aws syntax [-x|--excludes]"
  echo "  - (optional) Force write to output directory, even if it does not match the input name [-f|--force]"
  exit 1
fi

force_write="0"
excludes=()
optional_args=()
while test "$#" -gt 0; do
  key="$1"

  case $key in
    -x|--excludes)
      excludes+=("$2")
      shift # past argument
      shift # past value
      ;;
    -b|--bucket)
      bucket="$2"
      shift # past argument
      shift # past value
      ;;
    -n|--runfolder-name)
      runfolder_name="$2"
      shift # past argument
      shift # past value
      ;;
    -s|--source_path)
      source_path="$2"
      shift # past argument
      shift # past value
      ;;
    -d|--dest_path)
      dest_path="$2"
      shift # past argument
      shift # past value
      ;;
    -f|--force)
      force_write="1"
      shift # past argument
      ;;
    -k|--task-token)
      task_token="$2"
      shift # past argument
      shift # past value
      ;;
    *)    # unknown option (everything else)
      optional_args+=("$1") # save it in an array for later
      shift # past argument
      ;;
  esac
done
echo "Check parameters"

if test ! "$bucket"
then
  write_log "ERROR: Parameter 'bucket' missing"
  echo "You have to provide a bucket parameter!"
  exit 1
fi

if [[ -z "$runfolder_name" ]]
then
  write_log "ERROR: Parameter 'runfolder_name' missing"
  echo "You have to define a runfolder name!"
  exit 1
fi

if test ! "$dest_path"
then
  write_log "ERROR: Parameter 'dest-dir' missing"
  echo "You have to provide a dest-dir parameter!"
  exit 1
fi

if test ! "$source_path"
then
  write_log "ERROR: Parameter 'source-dir' missing"
  echo "You have to provide at least one source-dir parameter!"
  exit 1
fi

if test "${source_path#*$dest_path}" == "$source_path"
then
  write_log "WARNING: Destination $dest_path and source $source_path to not match!"
  if test $force_write = "0"
  then
    write_log "Aborting!"
    exit 1
  fi
fi

if [[ -z "$task_token" ]]
then
  write_log "ERROR: Parameter 'task_token' missing"
  echo "You have to provide an AWS SFN task token!"
  exit 1
fi


test_cmd="aws s3 ls s3://$bucket"
eval "$test_cmd"
ret_code=$?

if [ $ret_code != 0 ]; then
  write_log "ERROR: Could not access bucket $bucket."
  exit 1
fi

# configuring AWS S3 command
aws configure set default.s3.max_concurrent_requests 10
aws configure set default.s3.max_queue_size 10000
aws configure set default.s3.multipart_threshold 64MB
aws configure set default.s3.multipart_chunksize 16MB
aws configure set default.s3.max_bandwidth 800MB/s


# TODO: find a better place to store the log file
# TODO: perhaps add a timestamp to the log file name
log_file_name=$(echo "$dest_path" | tr \/ _)

# build the command
if test "$DEPLOY_ENV" = "prod"; then
  cmd="aws s3 sync --no-progress"
else
  cmd="aws s3 sync --no-progress --dryrun"
fi
for i in "${excludes[@]}"
do
  cmd+=" --exclude $i"
done
cmd+=" $source_path s3://$bucket/$dest_path >> $DIR/s3sync-logs/${log_file_name}.log"

write_log "INFO: Running: $cmd"
eval "$cmd"
if test "$?" == "0"; then
  status="success"
else
  status="failure"
  error_msg="AWS sync command failed!"
fi

# Finally complete the AWS Step Function pipeline task
# I.e. send a notification of compoletion (success/failure) to AWS

callback_cmd="aws stepfunctions"
if test "$status" == "success"; then 
  write_log "INFO: Reporting success to AWS pipeline..."
  callback_cmd+=" send-task-success --task-output '{\"runfolder\": \"$runfolder_name\"}'"
else 
  write_log "INFO: Reporting failure to AWS pipeline..."
  callback_cmd+=" send-task-failure --error '$error_msg'"
fi
callback_cmd+=" --task-token $task_token"

write_log "INFO: AWS command $callback_cmd"
eval "$callback_cmd"

write_log "INFO: All done."
