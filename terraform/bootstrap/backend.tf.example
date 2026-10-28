# -----------------------------------------------------------------------------
# bootstrap/backend.tf.example - the remote backend for THIS stack, used only
# after the first apply has created the bucket.
#
# What: once the bucket exists, copy this file to backend.tf and run
#       `terraform init -migrate-state` to move the local state into S3.
# Why:  the bootstrap stack creates the bucket its own state lives in, so it
#       can't use the bucket on its very first run. Ending with the state in S3
#       means it's versioned and not lost if your laptop is.
# Every other stack has its backend block from the start (in versions.tf).
# -----------------------------------------------------------------------------
terraform {
  backend "s3" {
    bucket       = "fs-tfstate-831547420318"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
