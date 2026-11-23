# ============================================================================
# IMS 2.0 - CloudWatch Monitoring & Alerting
# ============================================================================
# Comprehensive monitoring infrastructure for production applications

# ============================================================================
# SNS Topics for Notifications
# ============================================================================

resource "aws_sns_topic" "alerts" {
  name = "ims-alerts"

  tags = {
    Name = "ims-alerts-topic"
  }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic" "critical_alerts" {
  name = "ims-critical-alerts"

  tags = {
    Name = "ims-critical-alerts-topic"
  }
}

resource "aws_sns_topic_subscription" "critical_alerts_email" {
  topic_arn = aws_sns_topic.critical_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ============================================================================
# CloudWatch Log Groups
# ============================================================================

resource "aws_cloudwatch_log_group" "application" {
  name              = "/ims/application"
  retention_in_days = 30

  tags = {
    Name = "ims-application-logs"
  }
}

resource "aws_cloudwatch_log_group" "database" {
  name              = "/ims/database"
  retention_in_days = 30

  tags = {
    Name = "ims-database-logs"
  }
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/ims/api-gateway"
  retention_in_days = 7

  tags = {
    Name = "ims-api-gateway-logs"
  }
}

resource "aws_cloudwatch_log_group" "security" {
  name              = "/ims/security"
  retention_in_days = 90

  tags = {
    Name = "ims-security-logs"
  }
}

# ============================================================================
# RDS Monitoring
# ============================================================================

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "ims-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "Alert when RDS CPU exceeds 80%"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name          = "ims-rds-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "Alert when database connections exceed 80"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "ims-rds-storage-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = "300"
  statistic           = "Average"
  threshold           = "5368709120" # 5GB in bytes
  alarm_description   = "Alert when free storage is below 5GB"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_latency" {
  alarm_name          = "ims-rds-latency-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "ReadLatency"
  namespace           = "AWS/RDS"
  period              = "60"
  statistic           = "Average"
  threshold           = "10" # milliseconds
  alarm_description   = "Alert when read latency exceeds 10ms"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

# ============================================================================
# ElastiCache Redis Monitoring
# ============================================================================

resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "ims-redis-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = "300"
  statistic           = "Average"
  threshold           = "75"
  alarm_description   = "Alert when Redis CPU exceeds 75%"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_cluster.main.cluster_id
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  alarm_name          = "ims-redis-evictions"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = "300"
  statistic           = "Sum"
  threshold           = "100"
  alarm_description   = "Alert when Redis evictions occur"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_cluster.main.cluster_id
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "ims-redis-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = "300"
  statistic           = "Average"
  threshold           = "90"
  alarm_description   = "Alert when Redis memory usage exceeds 90%"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_cluster.main.cluster_id
  }
}

# ============================================================================
# Application Health Monitoring
# ============================================================================

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "ims-api-errors-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "HTTPCode_Backend_5XX"
  namespace           = "AWS/ApplicationELB"
  period              = "300"
  statistic           = "Sum"
  threshold           = "50"
  alarm_description   = "Alert when 5XX errors exceed 50 in 5 minutes"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    LoadBalancer = "app/ims-alb/1234567890abcdef"
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "ims-api-latency-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = "60"
  statistic           = "Average"
  threshold           = "2" # seconds
  alarm_description   = "Alert when API response time exceeds 2 seconds"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = "app/ims-alb/1234567890abcdef"
  }
}

# ============================================================================
# CloudWatch Dashboard
# ============================================================================

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "ims-main-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/RDS", "CPUUtilization", { stat = "Average" }],
            [".", "DatabaseConnections", { stat = "Average" }],
            [".", "ReadLatency", { stat = "Average" }],
            [".", "WriteLatency", { stat = "Average" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "RDS Performance"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/ElastiCache", "CPUUtilization", { stat = "Average" }],
            [".", "DatabaseMemoryUsagePercentage", { stat = "Average" }],
            [".", "CacheHits", { stat = "Sum" }],
            [".", "CacheMisses", { stat = "Sum" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Redis Cache Performance"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", { stat = "Sum" }],
            [".", "HTTPCode_Backend_2XX", { stat = "Sum" }],
            [".", "HTTPCode_Backend_4XX", { stat = "Sum" }],
            [".", "HTTPCode_Backend_5XX", { stat = "Sum" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "API Request Distribution"
        }
      },
      {
        type = "log"
        properties = {
          query   = "fields @timestamp, @message | stats count() by @logStream"
          region  = var.aws_region
          title   = "Log Events"
        }
      }
    ]
  })
}

# ============================================================================
# Log Metric Filters & Alarms
# ============================================================================

resource "aws_cloudwatch_log_metric_filter" "application_errors" {
  name           = "ims-application-errors"
  log_group_name = aws_cloudwatch_log_group.application.name
  filter_pattern = "[ERROR], [CRITICAL]"

  metric_transformation {
    name      = "ApplicationErrorCount"
    namespace = "IMS/Application"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "application_errors_alarm" {
  alarm_name          = "ims-application-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "ApplicationErrorCount"
  namespace           = "IMS/Application"
  period              = "300"
  statistic           = "Sum"
  threshold           = "10"
  alarm_description   = "Alert when application errors exceed 10 in 5 minutes"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_log_metric_filter" "unauthorized_access" {
  name           = "ims-unauthorized-access"
  log_group_name = aws_cloudwatch_log_group.security.name
  filter_pattern = "[401], [403], [UNAUTHORIZED]"

  metric_transformation {
    name      = "UnauthorizedAccessCount"
    namespace = "IMS/Security"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "unauthorized_access_alarm" {
  alarm_name          = "ims-unauthorized-access"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "UnauthorizedAccessCount"
  namespace           = "IMS/Security"
  period              = "300"
  statistic           = "Sum"
  threshold           = "5"
  alarm_description   = "Alert on suspicious unauthorized access patterns"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
}

# ============================================================================
# Outputs
# ============================================================================

output "sns_topic_alerts_arn" {
  description = "SNS topic ARN for alerts"
  value       = aws_sns_topic.alerts.arn
}

output "sns_topic_critical_alerts_arn" {
  description = "SNS topic ARN for critical alerts"
  value       = aws_sns_topic.critical_alerts.arn
}

output "cloudwatch_dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=ims-main-dashboard"
}
