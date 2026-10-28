# -----------------------------------------------------------------------------
# 05-registry/main.tf - ECR repositories for the simulator and consumer images.
#
# What: one private repository per service with immutable tags, scan on push
#       and a lifecycle policy.
# Why:  immutable tags + tagging every image with its commit SHA means a tag can
#       never be overwritten, so "which code is running?" always has one answer
#       (lineage that can't lie). The lifecycle policy caps storage cost.
# Stack type: PERSISTENT. Cost: $0.10/GB-month - a few cents for these images.
# Reused from clinical-trial-matching (05-registry), with fraudstream repos.
# -----------------------------------------------------------------------------
resource "aws_ecr_repository" "this" {
  #checkov:skip=CKV_AWS_136:AES256 (ECR default) is sufficient for images built from a public repo; avoids KMS cost
  for_each = var.repositories

  name                 = "${var.project_name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  lifecycle {
    prevent_destroy = true # deleting a repository deletes every image in it
  }
}

locals {
  lifecycle_policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the most recent ${var.images_to_keep} images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.images_to_keep
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy     = local.lifecycle_policy
}
