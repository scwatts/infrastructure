#!/usr/bin/env python3
from aws_cdk import core
from stacks.orchestrator import OrchestratorStack

echo_tes_dev_props = {
    'namespace': 'showcase-iap-tes-dev',
    'iap_api_base_url': 'aps2.platform.illumina.com',
    'task_id': 'tdn.d4425331e3ba4779adafbf31176f0580',
    'ssm_param_name': '/iap/jwt-token',
    'ssm_param_version': 1,
    'gds_log_folder': 'gds://teslogs/ShowCase/',
    'gds_run_volume': 'gds://umccr-run-data-dev',
    's3_run_bucket': 'umccr-run-data-dev'
}


app = core.App()

OrchestratorStack(
    app,
    echo_tes_dev_props['namespace'],
    echo_tes_dev_props,
    env={'account': '843407916570', 'region': 'ap-southeast-2'}
)

app.synth()
