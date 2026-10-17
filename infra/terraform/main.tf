locals {
  buckets = var.enabled ? toset(["lake", "published", "logs"]) : toset([])
  count   = var.enabled && var.enable_app ? 1 : 0
}
resource "aws_s3_bucket" "data" {
  for_each      = local.buckets
  bucket        = "pokermind-${var.account_id}-${var.region}-${each.key}"
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_public_access_block" "data" {
  for_each                = aws_s3_bucket.data
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_policy" "tls" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid      = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*",
    Resource = [each.value.arn, "${each.value.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_ecr_repository" "app" {
  # Foundation first, then push a tested image, then enable the app VM.
  count                = var.enabled ? 1 : 0
  name                 = "pokermind-app"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_cloudwatch_log_group" "app" {
  count             = local.count
  name              = "/pokermind/app"
  retention_in_days = 14
}
resource "aws_iam_role" "app" {
  count = local.count
  name  = "pokermind-private-app"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "ssm" {
  count      = local.count
  role       = aws_iam_role.app[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "app" {
  count = local.count
  role  = aws_iam_role.app[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.data["published"].arn}/snapshots/*"] },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.data["published"].arn], Condition = { StringLike = { "s3:prefix" = ["snapshots/*"] } } },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.app[0].arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.app[0].arn}:*" }
  ] })
}
resource "aws_iam_instance_profile" "app" {
  count = local.count
  name  = "pokermind-private-app"
  role  = aws_iam_role.app[0].name
}
resource "aws_security_group" "app" {
  count       = local.count
  name_prefix = "pokermind-private-"
  vpc_id      = var.vpc_id
  description = "No inbound access; SSM over outbound HTTPS only"
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_instance" "app" {
  count                                = local.count
  ami                                  = var.ami_id
  instance_type                        = var.instance_type
  subnet_id                            = var.subnet_id
  vpc_security_group_ids               = [aws_security_group.app[0].id]
  associate_public_ip_address          = true
  iam_instance_profile                 = aws_iam_instance_profile.app[0].name
  instance_initiated_shutdown_behavior = "stop"
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
  }
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    region           = var.region, registry = split("/", aws_ecr_repository.app[0].repository_url)[0],
    image            = "${aws_ecr_repository.app[0].repository_url}@${var.image_digest}", minutes = var.run_minutes,
    log_group        = aws_cloudwatch_log_group.app[0].name,
    published_bucket = aws_s3_bucket.data["published"].id,
    snapshot_sha256  = var.snapshot_sha256, publication_sha256 = var.publication_sha256
  })
  tags = { Name = "pokermind-private-app" }
  lifecycle {
    precondition {
      condition     = var.vpc_id != "" && var.subnet_id != "" && var.ami_id != "" && var.expires_at != "UNAPPROVED"
      error_message = "Reviewed network, AMI, and expiry are required."
    }
  }
}
output "instance_id" { value = try(aws_instance.app[0].id, null) }
output "image_repository" { value = try(aws_ecr_repository.app[0].repository_url, null) }
output "retained_storage" { value = "S3 versions, ECR images, CloudWatch logs and the encrypted root disk survive compute stop. Budget retained resources separately." }
