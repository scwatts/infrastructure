# TODO: check used resource names are unique enough to not clash with other stacks
################################################################################
# create ECS instance profile for compute resources
data "template_file" "assume_role_ec2" {
  template = "${file("${path.module}/policies/assume-role.json")}"

  vars {
    service = "ec2.amazonaws.com"
  }
}

resource "aws_iam_policy" "assume_role_ec2" {
  path   = "/"
  policy = "${data.template_file.assume_role_ec2.rendered}"
}

resource "aws_iam_role" "ecs_instance_role" {
  name               = "ecs_instance_role${var.name_suffix}"
  assume_role_policy = "${aws_iam_policy.assume_role_ec2}"
}

resource "aws_iam_policy" "umccr_container_service_policy" {
  name   = "umccr_container_service_policy${var.name_suffix}"
  path   = "/"
  policy = "${file("${path.module}/policies/AmazonEC2ContainerServiceforEC2Role.json")}"
}

resource "aws_iam_role_policy_attachment" "ecs_instance_role" {
  role       = "${aws_iam_role.ecs_instance_role.name}"
  policy_arn = "${aws_iam_policy.umccr_container_service_policy.arn}"
}

resource "aws_iam_instance_profile" "ecs_instance_profile" {
  name = "ecs_instance_profile${var.name_suffix}"
  role = "${aws_iam_role.ecs_instance_role.name}"
}

################################################################################
# create compute environment service role

data "template_file" "assume_role_batch" {
  template = "${file("${path.module}/policies/assume-role.json")}"

  vars {
    service = "batch.amazonaws.com"
  }
}

resource "aws_iam_policy" "assume_role_batch" {
  path   = "/"
  policy = "${data.template_file.assume_role_batch.rendered}"
}

resource "aws_iam_role" "aws_batch_service_role" {
  name               = "aws_batch_service_role${var.name_suffix}"
  assume_role_policy = "${aws_iam_policy.assume_role_batch}"
}

resource "aws_iam_policy" "umccr_batch_policy" {
  name   = "umccr_batch_policy${var.name_suffix}"
  path   = "/"
  policy = "${file("${path.module}/policies/AWSBatchServiceRole.json")}"
}

resource "aws_iam_role_policy_attachment" "aws_batch_service_role" {
  role       = "${aws_iam_role.aws_batch_service_role.name}"
  policy_arn = "${aws_iam_policy.umccr_batch_policy.arn}"
}

################################################################################
# create SPOT fleet service role

data "template_file" "assume_role_spotfleet" {
  template = "${file("${path.module}/policies/assume-role.json")}"

  vars {
    service = "spotfleet.amazonaws.com"
  }
}

resource "aws_iam_policy" "assume_role_spotfleet" {
  path   = "/"
  policy = "${data.template_file.assume_role_spotfleet.rendered}"
}

resource "aws_iam_role" "aws_spotfleet_service_role" {
  name               = "aws_spotfleet_service_role${var.name_suffix}"
  assume_role_policy = "${aws_iam_policy.assume_role_spotfleet}"
}

resource "aws_iam_policy" "umccr_spotfleet_policy" {
  name   = "umccr_spotfleet_policy${var.name_suffix}"
  path   = "/"
  policy = "${file("${path.module}/policies/AmazonEC2SpotFleetTaggingRole.json")}"
}

resource "aws_iam_role_policy_attachment" "aws_spotfleet_service_role" {
  role       = "${aws_iam_role.aws_spotfleet_service_role.name}"
  policy_arn = "${aws_iam_policy.umccr_spotfleet_policy.arn}"
}

################################################################################
# create ECS compute environment

resource "aws_batch_compute_environment" "batch" {
  compute_environment_name = "${var.compute_env_name}"

  compute_resources {
    instance_role = "${aws_iam_instance_profile.ecs_instance_profile.arn}"
    image_id      = "${var.image_id}"

    instance_type = "${var.instance_types}"

    max_vcpus     = 16
    desired_vcpus = 8
    min_vcpus     = 0

    security_group_ids = "${var.security_group_ids}"

    subnets = "${var.subnet_ids}"

    tags = {
      Name = "batch"
    }

    spot_iam_fleet_role = "${aws_iam_role.aws_spotfleet_service_role.arn}"
    type                = "SPOT"
    bid_percentage      = 50
  }

  service_role = "${aws_iam_role.aws_batch_service_role.arn}"
  type         = "MANAGED"
  depends_on   = ["aws_iam_role_policy_attachment.aws_batch_service_role"]
}
