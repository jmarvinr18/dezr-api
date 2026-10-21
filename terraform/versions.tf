terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # State holds the generated passwords. For a team, keep it in an encrypted S3 backend:
  # backend "s3" {
  #   bucket  = "my-terraform-state"
  #   key     = "dezr-api/terraform.tfstate"
  #   region  = "ap-southeast-1"
  #   encrypt = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge({ Project = var.name, ManagedBy = "terraform" }, var.tags)
  }
}
