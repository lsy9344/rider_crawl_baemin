# GitHub-hosted Actions deploy path.
#
# The workflow builds the ARM64 server image outside the production EC2 host,
# pushes it to ECR, then uses SSM RunCommand to ask the EC2 instance to pull and
# restart the compose stack with --no-build. This removes the self-hosted runner
# and Docker build pressure from the small production box.

locals {
  github_deploy_sub = "repo:${var.github_repository}:ref:refs/heads/${var.github_deploy_branch}"
}

resource "aws_ecr_repository" "server" {
  name                 = var.server_ecr_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = var.server_ecr_repository_name }
}

resource "aws_ecr_lifecycle_policy" "server" {
  repository = aws_ecr_repository.server.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the most recent 30 production images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      }
    ]
  })
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_deploy_sub]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  name               = "${var.project}-github-deploy-role"
  assume_role_policy = data.aws_iam_policy_document.github_actions_deploy_assume.json
}

data "aws_iam_policy_document" "github_actions_deploy" {
  statement {
    sid       = "ECRAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "ECRPushServerImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.server.arn]
  }

  statement {
    sid     = "SendDeployCommand"
    actions = ["ssm:SendCommand"]
    resources = [
      aws_instance.app.arn,
      "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
    ]
  }

  statement {
    sid = "ReadDeployCommandResult"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "ReadProductionHostAlarms"
    actions = ["cloudwatch:DescribeAlarms"]
    resources = [
      "arn:aws:cloudwatch:${var.region}:${data.aws_caller_identity.current.account_id}:alarm:${var.project}-host-mem-available-low",
      "arn:aws:cloudwatch:${var.region}:${data.aws_caller_identity.current.account_id}:alarm:${var.project}-host-swap-used-high",
    ]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "${var.project}-github-deploy-policy"
  role   = aws_iam_role.github_actions_deploy.id
  policy = data.aws_iam_policy_document.github_actions_deploy.json
}
