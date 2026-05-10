# =========================================
# Orchestration: Deploy collection script to ab1vm and trigger collection
# Prerequisites: Run context.ps1 and setup-sre-storage.ps1 first
# Usage:
#   .\deploy-and-collect.ps1 -Action deploy    # One-time: push script to ab1vm
#   .\deploy-and-collect.ps1 -Action collect    # Trigger config collection
#   .\deploy-and-collect.ps1 -Action verify     # Check blob storage for results
#   .\deploy-and-collect.ps1 -Action cron       # Set up weekly cron on ab1vm
# =========================================

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("deploy", "collect", "verify", "cron")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

# --- Configuration ---
$JumphostRG    = "RG_SharedServices_CUS"
$JumphostVM    = "jumphostvm01"
$TargetVM      = "ab1vm"
$ScriptName    = "collect-sap-configs.sh"
$LocalScript   = Join-Path $PSScriptRoot $ScriptName
$RemotePath    = "/opt/sre/$ScriptName"
$StorageAccount = "stsre3configs"
$Container      = "sap-configs"

# Get jumphost private IP for SSH proxy
function Get-JumphostIP {
    $ip = az vm show -g $JumphostRG -n $JumphostVM -d --query "privateIps" -o tsv
    if (-not $ip) { throw "Could not get jumphost IP. Is $JumphostVM running?" }
    return $ip
}

switch ($Action) {
    "deploy" {
        Write-Host "=== Deploying collection script to $TargetVM ===" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "This will:" -ForegroundColor Yellow
        Write-Host "  1. Copy $ScriptName to $TargetVM at $RemotePath"
        Write-Host "  2. Make it executable"
        Write-Host "  3. Verify az CLI is available on the VM"
        Write-Host ""

        if (-not (Test-Path $LocalScript)) {
            throw "Local script not found: $LocalScript"
        }

        $jumphostIP = Get-JumphostIP
        Write-Host "Jumphost IP: $jumphostIP"
        Write-Host ""

        # Instructions for manual SSH deployment (since SSH through jumphost needs interactive auth)
        Write-Host "Run these commands manually from your terminal:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  # SSH to jumphost first" -ForegroundColor Gray
        Write-Host "  ssh $jumphostIP" -ForegroundColor White
        Write-Host ""
        Write-Host "  # From jumphost, create directory and copy script to ab1vm" -ForegroundColor Gray
        Write-Host "  ssh $TargetVM 'sudo mkdir -p /opt/sre'" -ForegroundColor White
        Write-Host ""
        Write-Host "  # Exit back to local, then SCP via jumphost" -ForegroundColor Gray
        Write-Host "  scp -J $jumphostIP $($LocalScript -replace '\\','/') ${TargetVM}:/tmp/$ScriptName" -ForegroundColor White
        Write-Host ""
        Write-Host "  # SSH to ab1vm via jumphost and move script into place" -ForegroundColor Gray
        Write-Host "  ssh -J $jumphostIP $TargetVM" -ForegroundColor White
        Write-Host "  sudo mv /tmp/$ScriptName $RemotePath" -ForegroundColor White
        Write-Host "  sudo chmod +x $RemotePath" -ForegroundColor White
        Write-Host "  az --version  # Verify az CLI is installed" -ForegroundColor White
        Write-Host ""
        Write-Host "If az CLI is not installed, run:" -ForegroundColor Yellow
        Write-Host "  sudo zypper install -y azure-cli" -ForegroundColor White
    }

    "collect" {
        Write-Host "=== Triggering Config Collection on $TargetVM ===" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Using Azure VM Run Command to trigger collection..." -ForegroundColor Yellow

        # Use Run Command to trigger the script (avoids SSH dependency for scheduled runs)
        az vm run-command invoke `
            -g "RG_SAP_CUS_AB1" `
            -n "AB1vm" `
            --command-id RunShellScript `
            --scripts "sudo $RemotePath" `
            --query "value[0].message" `
            -o tsv

        if ($LASTEXITCODE -eq 0) {
            Write-Host "`nCollection triggered successfully." -ForegroundColor Green
            Write-Host "Run '.\deploy-and-collect.ps1 -Action verify' to check results."
        } else {
            Write-Host "`nCollection may have failed. Check VM logs at /var/log/sre-config-collect.log" -ForegroundColor Red
        }
    }

    "verify" {
        Write-Host "=== Verifying Blob Storage Contents ===" -ForegroundColor Cyan
        Write-Host ""

        Write-Host "Latest configs (${Container}/${TargetVM}/latest/):" -ForegroundColor Yellow
        az storage blob list `
            --account-name $StorageAccount `
            --container-name $Container `
            --prefix "${TargetVM}/latest/" `
            --auth-mode login `
            --query "[].{Name:name, Size:properties.contentLength, Modified:properties.lastModified}" `
            -o table

        Write-Host "`nArchives:" -ForegroundColor Yellow
        az storage blob list `
            --account-name $StorageAccount `
            --container-name $Container `
            --prefix "${TargetVM}/" `
            --auth-mode login `
            --query "[?ends_with(name, '.tar.gz')].{Name:name, Size:properties.contentLength, Modified:properties.lastModified}" `
            -o table
    }

    "cron" {
        Write-Host "=== Setting up Weekly Cron Job on $TargetVM ===" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "This will add a weekly cron job (Sunday 2:00 AM) on $TargetVM." -ForegroundColor Yellow
        Write-Host ""

        $cronEntry = "0 2 * * 0 /opt/sre/$ScriptName >> /var/log/sre-config-collect.log 2>&1"

        az vm run-command invoke `
            -g "RG_SAP_CUS_AB1" `
            -n "AB1vm" `
            --command-id RunShellScript `
            --scripts "(crontab -l 2>/dev/null | grep -v '$ScriptName'; echo '$cronEntry') | crontab -" `
            --query "value[0].message" `
            -o tsv

        if ($LASTEXITCODE -eq 0) {
            Write-Host "`nCron job configured." -ForegroundColor Green
            Write-Host "Schedule: Every Sunday at 2:00 AM"
            Write-Host "Log: /var/log/sre-config-collect.log"
        } else {
            Write-Host "`nFailed to set up cron. You can manually add:" -ForegroundColor Red
            Write-Host "  $cronEntry"
        }
    }
}
