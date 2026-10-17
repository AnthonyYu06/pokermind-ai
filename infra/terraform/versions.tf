terraform {
  required_version = "= 1.16.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Use backend.tf.example only after a reviewed state bucket exists.
}
provider "aws" {
  region              = var.region
  allowed_account_ids = var.enabled ? [var.account_id] : null
  default_tags {
    tags = { Project = "pokermind", Environment = "learning", Owner = var.owner, ExpiresAt = var.expires_at }
  }
}
