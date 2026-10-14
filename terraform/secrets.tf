# ==============================================================================
# AWS Secrets Manager Secrets
# ==============================================================================
# Securely stores API keys for external systems (PIM and WMS APIs).
# Decouples secret rotation from application code and infrastructure deployments.
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Product API Key Secret (PIM)
# ------------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "product_api_key" {
  name                    = "${local.name_prefix}-product-api-key"
  description             = "Authentication API key for the Product Information Management (PIM) system"
  recovery_window_in_days = 0 # Immediate deletion on destroy in dev environments

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-product-api-key"
    Description = "Secret holding PIM API authentication key"
  })
}

resource "aws_secretsmanager_secret_version" "product_api_key" {
  secret_id     = aws_secretsmanager_secret.product_api_key.id
  secret_string = "challenge-product-key" # Default placeholder value to allow deployment
}

# ------------------------------------------------------------------------------
# 2. Warehouse API Key Secret (WMS)
# ------------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "warehouse_api_key" {
  name                    = "${local.name_prefix}-warehouse-api-key"
  description             = "Authentication API key for the Warehouse Management System (WMS)"
  recovery_window_in_days = 0

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-warehouse-api-key"
    Description = "Secret holding WMS API authentication key"
  })
}

resource "aws_secretsmanager_secret_version" "warehouse_api_key" {
  secret_id     = aws_secretsmanager_secret.warehouse_api_key.id
  secret_string = "challenge-warehouse-key" # Default placeholder value to allow deployment
}
