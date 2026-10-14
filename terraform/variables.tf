# ==============================================================================
# Variables Definition
# ==============================================================================

variable "aws_region" {
  description = "Target AWS region for deploying catalogue sync infrastructure."
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Deployment environment name (e.g., dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project identifier used in resource names and tagging."
  type        = string
  default     = "catalogue-sync"
}

variable "product_api_url" {
  description = "Base URL endpoint for the Product Information Management (PIM) API."
  type        = string
  default     = "https://api.pim.internal"
}

variable "warehouse_api_url" {
  description = "Base URL endpoint for the Warehouse Management System (WMS) API."
  type        = string
  default     = "https://api.wms.internal"
}

variable "s3_retention_days" {
  description = "Number of days to retain exported catalogue CSV files in S3 before expiration."
  type        = number
  default     = 90

  validation {
    condition     = var.s3_retention_days >= 30
    error_message = "S3 retention must be at least 30 days to meet compliance and audit requirements."
  }
}

variable "lambda_memory" {
  description = "Default memory allocation (in MB) for Lambda functions unless overridden."
  type        = number
  default     = 256
}

variable "lambda_timeout" {
  description = "Default execution timeout (in seconds) for Lambda functions unless overridden."
  type        = number
  default     = 300
}

variable "sync_concurrency" {
  description = "Reserved concurrent executions for WMS sync batch worker Lambda to respect rate limits."
  type        = number
  default     = 5

  validation {
    condition     = var.sync_concurrency >= 1 && var.sync_concurrency <= 20
    error_message = "Sync concurrency must be between 1 and 20 to protect WMS API rate limits (max 20 RPS)."
  }
}

# ------------------------------------------------------------------------------
# ECS Task Variables
# ------------------------------------------------------------------------------

variable "ecs_image" {
  description = "Container image URI for the catalogue export ECS task."
  type        = string
  default     = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/catalogue-sync:latest"
}

variable "ecs_cpu" {
  description = "CPU units allocated for the Fargate export task (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "ecs_memory" {
  description = "Memory allocated (in MB) for the Fargate export task (512 = 0.5 GB)."
  type        = number
  default     = 512
}

# ------------------------------------------------------------------------------
# Observability & Monitoring Variables
# ------------------------------------------------------------------------------

variable "log_retention_days" {
  description = "Retention period in days for CloudWatch log groups across all components."
  type        = number
  default     = 30
}

variable "alert_email" {
  description = "Optional email address to subscribe to SNS pipeline alert notifications."
  type        = string
  default     = ""
}
