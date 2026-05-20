#!/bin/bash
# =========================================
# SAP Configuration Collector — Multi-SID Version
# Deploy to: /opt/sre/collect-sap-configs.sh on ALL SAP VMs
#
# Usage:
#   collect-sap-configs.sh --sid AB1 --db-sid DB1 --roles db,ascs,app \
#       --hana-inst 00 --ascs-inst 01 --app-inst 02
#   collect-sap-configs.sh --sid AB2 --db-sid AB2 --roles db --hana-inst 00
#   collect-sap-configs.sh --sid AB2 --db-sid AB2 --roles ascs --ascs-inst 00
#   collect-sap-configs.sh --sid HSO --db-sid HSO --roles db --hana-inst 00
#   collect-sap-configs.sh --roles sbd   (SBD nodes — no SAP SID needed)
#
# Run as root (or with sudo) for access to all config files
# =========================================

set -euo pipefail

# --- Defaults (override via environment variables or parameters) ---
STORAGE_ACCOUNT="${SRE_STORAGE_ACCOUNT:?ERROR: Set SRE_STORAGE_ACCOUNT environment variable}"
CONTAINER="${SRE_CONTAINER:-sap-configs}"
UMI_CLIENT_ID="${SRE_UMI_CLIENT_ID:?ERROR: Set SRE_UMI_CLIENT_ID environment variable}"
VM_NAME=$(hostname -s)
if [ -z "$VM_NAME" ]; then
    echo "ERROR: hostname returned empty"
    exit 1
fi
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
DATE_DIR=$(date +%Y-%m-%d)
LOG_FILE="/opt/sre/collector.log"
STAGING_DIR="/opt/sre/staging"

SAP_SID=""
DB_SID=""
ROLES=""
HANA_INSTANCE=""
ASCS_INSTANCE=""
APP_INSTANCE=""

# --- Parse Arguments ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --sid)        SAP_SID="$2"; shift 2 ;;
        --db-sid)     DB_SID="$2"; shift 2 ;;
        --roles)      ROLES="$2"; shift 2 ;;
        --hana-inst)  HANA_INSTANCE="$2"; shift 2 ;;
        --ascs-inst)  ASCS_INSTANCE="$2"; shift 2 ;;
        --app-inst)   APP_INSTANCE="$2"; shift 2 ;;
        --vm-name)    VM_NAME="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Validate
if [ -z "$ROLES" ]; then
    echo "ERROR: --roles is required (db, ascs, app, sbd, or comma-separated)"
    exit 1
fi

if [ "$ROLES" != "sbd" ] && [ -z "$SAP_SID" ]; then
    echo "ERROR: --sid is required for non-SBD roles"
    exit 1
fi

# Default DB_SID to SAP_SID if not specified
[ -z "$DB_SID" ] && DB_SID="$SAP_SID"

# Mirror blob hierarchy locally: /opt/sre/sap-configs/{SID}/{hostname}/latest/
BLOB_SID="${SAP_SID:-SBD}"
BLOB_PREFIX="${BLOB_SID}/${VM_NAME}"
COLLECT_DIR="/opt/sre/sap-configs/${BLOB_PREFIX}/latest"
ARCHIVE_NAME="sap-configs-${VM_NAME}-${TIMESTAMP}.tar.gz"

# Ensure directories exist
mkdir -p "$COLLECT_DIR" "$STAGING_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

collect_file() {
    local src="$1"
    local dest_subdir="$2"
    local dest_dir="${COLLECT_DIR}/${dest_subdir}"

    if [ -f "$src" ]; then
        mkdir -p "$dest_dir"
        cp "$src" "$dest_dir/"
        log "  OK: $src"
    elif [ -d "$src" ]; then
        mkdir -p "$dest_dir"
        find "$src" -maxdepth 1 -type f \( -name "*.PFL" -o -name "*.pfl" -o -name "*_*_*" -o -name "DEFAULT*" -o -name "*.ini" -o -name "*.cfg" -o -name "*.conf" -o -name "dev_*" -o -name "*.log" \) -size -5M -exec cp {} "$dest_dir/" \;
        log "  OK: $src (directory)"
    else
        log "  SKIP: $src (not found)"
    fi
}

# --- Helper: check if role is in the comma-separated list ---
has_role() {
    echo ",$ROLES," | grep -q ",$1,"
}

log "========================================="
log "SAP Config Collection Started"
log "VM: ${VM_NAME} | SID: ${SAP_SID:-N/A} | DB_SID: ${DB_SID:-N/A} | Roles: ${ROLES}"
log "Local path: /opt/sre/sap-configs/${BLOB_PREFIX}/latest/"
log "========================================="

# Clear previous latest/ and recreate (ensures no stale files)
rm -rf "${COLLECT_DIR}"
mkdir -p "${COLLECT_DIR}"

# =========================================
# Role: db — HANA Database configs
# =========================================
if has_role "db" && [ -n "$DB_SID" ]; then
    log "=== Collecting HANA DB configs (${DB_SID}) ==="
    mkdir -p "${COLLECT_DIR}/hana"

    # Collect ALL custom .ini files (global.ini, nameserver.ini, indexserver.ini, daemon.ini,
    # xsengine.ini, scriptserver.ini, dpserver.ini, statisticsserver.ini, etc.)
    if [ -d "/usr/sap/${DB_SID}/SYS/global/hdb/custom/config" ]; then
        find /usr/sap/${DB_SID}/SYS/global/hdb/custom/config -maxdepth 1 -name "*.ini" -exec cp {} "${COLLECT_DIR}/hana/" \;
        log "  OK: /usr/sap/${DB_SID}/SYS/global/hdb/custom/config/*.ini"
    fi
    # DB-specific (tenant) config
    collect_file "/usr/sap/${DB_SID}/SYS/global/hdb/custom/config/DB_${DB_SID}/global.ini" "hana/db-specific"

    # HANA instance profile — try both lowercase and uppercase VM name
    if [ -n "$HANA_INSTANCE" ]; then
        collect_file "/usr/sap/${DB_SID}/SYS/profile/${DB_SID}_HDB${HANA_INSTANCE}_${VM_NAME}" "hana"
        upper_vm=$(echo "${VM_NAME}" | tr '[:lower:]' '[:upper:]')
        if [ "$upper_vm" != "$VM_NAME" ]; then
            collect_file "/usr/sap/${DB_SID}/SYS/profile/${DB_SID}_HDB${HANA_INSTANCE}_${upper_vm}" "hana"
        fi
    fi

    # HANA backup config
    collect_file "/usr/sap/${DB_SID}/SYS/global/hdb/custom/config/backint.cfg" "hana"

    # hdbuserstore keys (connection config — no passwords exposed)
    if command -v hdbuserstore &>/dev/null; then
        timeout 15 hdbuserstore list > "${COLLECT_DIR}/hana/hdbuserstore-list.txt" 2>/dev/null || log "  WARN: hdbuserstore list failed or timed out"
        log "  OK: hdbuserstore list"
    fi

    # HANA landscape info
    if [ -n "$HANA_INSTANCE" ]; then
        timeout 15 su - ${DB_SID,,}adm -c "python /usr/sap/${DB_SID}/HDB${HANA_INSTANCE}/exe/python_support/landscapeHostConfiguration.py" > "${COLLECT_DIR}/hana/landscape-host-config.txt" 2>/dev/null || log "  WARN: landscapeHostConfiguration failed or timed out"
    fi

    # Cluster configs (relevant for DB HA)
    log "Collecting cluster configs..."
    mkdir -p "${COLLECT_DIR}/cluster"
    collect_file "/etc/corosync/corosync.conf" "cluster"
    collect_file "/etc/sysconfig/sbd" "cluster"

    if command -v crm &>/dev/null; then
        timeout 15 crm configure show > "${COLLECT_DIR}/cluster/crm-config.txt" 2>/dev/null || log "  WARN: crm configure show failed or timed out"
        timeout 15 crm status > "${COLLECT_DIR}/cluster/crm-status.txt" 2>/dev/null || true
        log "  OK: crm configure/status"
    elif command -v pcs &>/dev/null; then
        timeout 15 pcs config show > "${COLLECT_DIR}/cluster/pcs-config.txt" 2>/dev/null || log "  WARN: pcs config show failed or timed out"
        timeout 15 pcs status > "${COLLECT_DIR}/cluster/pcs-status.txt" 2>/dev/null || true
        log "  OK: pcs config/status"
    fi

    if command -v SAPHanaSR-showAttr &>/dev/null; then
        timeout 15 SAPHanaSR-showAttr > "${COLLECT_DIR}/cluster/saphanasr-showattr.txt" 2>/dev/null || log "  WARN: SAPHanaSR-showAttr failed or timed out"
        log "  OK: SAPHanaSR-showAttr"
    fi

    # Pacemaker sysconfig
    collect_file "/etc/sysconfig/pacemaker" "cluster"

    # Corosync quorum status
    if command -v corosync-quorumtool &>/dev/null; then
        timeout 10 corosync-quorumtool -s > "${COLLECT_DIR}/cluster/corosync-quorum.txt" 2>/dev/null || log "  WARN: corosync-quorumtool failed or timed out"
        log "  OK: corosync-quorumtool"
    fi

    # Resource agent and fence agent versions
    {
        echo "=== Resource Agents ==="
        rpm -qa 2>/dev/null | grep -i resource-agents || dpkg -l 2>/dev/null | grep resource-agents || echo "N/A"
        echo ""
        echo "=== Fence Agents ==="
        rpm -qa 2>/dev/null | grep -i fence-agents || echo "N/A"
        echo ""
        echo "=== SAPHanaSR ==="
        rpm -qa 2>/dev/null | grep -i SAPHanaSR || echo "N/A"
    } > "${COLLECT_DIR}/cluster/agent-versions.txt"
    log "  OK: cluster agent versions"
fi

# =========================================
# Role: ascs — SAP Central Services configs
# =========================================
if has_role "ascs" && [ -n "$SAP_SID" ]; then
    log "=== Collecting ASCS configs (${SAP_SID}) ==="
    mkdir -p "${COLLECT_DIR}/sap-profiles"

    collect_file "/usr/sap/${SAP_SID}/SYS/profile/DEFAULT.PFL" "sap-profiles"
    collect_file "/sapmnt/${SAP_SID}/profile" "sap-profiles/sapmnt"

    if [ -n "$ASCS_INSTANCE" ]; then
        collect_file "/usr/sap/${SAP_SID}/SYS/profile/${SAP_SID}_ASCS${ASCS_INSTANCE}_${VM_NAME}" "sap-profiles"
        upper_vm=$(echo "${VM_NAME}" | tr '[:lower:]' '[:upper:]')
        if [ "$upper_vm" != "$VM_NAME" ]; then
            collect_file "/usr/sap/${SAP_SID}/SYS/profile/${SAP_SID}_ASCS${ASCS_INSTANCE}_${upper_vm}" "sap-profiles"
        fi

        # ASCS work directory logs
        mkdir -p "${COLLECT_DIR}/sap-work/ascs"
        for f in dev_w0 dev_disp dev_ms dev_rd dev_enqsrv; do
            collect_file "/usr/sap/${SAP_SID}/ASCS${ASCS_INSTANCE}/work/${f}" "sap-work/ascs"
        done
    fi

    # Cluster configs (relevant for ASCS HA — enqueue replication)
    if [ ! -d "${COLLECT_DIR}/cluster" ]; then
        mkdir -p "${COLLECT_DIR}/cluster"
        collect_file "/etc/corosync/corosync.conf" "cluster"
        collect_file "/etc/sysconfig/sbd" "cluster"
        if command -v crm &>/dev/null; then
            timeout 15 crm configure show > "${COLLECT_DIR}/cluster/crm-config.txt" 2>/dev/null || true
            timeout 15 crm status > "${COLLECT_DIR}/cluster/crm-status.txt" 2>/dev/null || true
        fi
    fi
fi

# =========================================
# Role: app — SAP Application Server configs
# =========================================
if has_role "app" && [ -n "$SAP_SID" ]; then
    log "=== Collecting App Server configs (${SAP_SID}) ==="
    mkdir -p "${COLLECT_DIR}/sap-profiles"

    collect_file "/usr/sap/${SAP_SID}/SYS/profile/DEFAULT.PFL" "sap-profiles"

    if [ -n "$APP_INSTANCE" ]; then
        collect_file "/usr/sap/${SAP_SID}/SYS/profile/${SAP_SID}_D${APP_INSTANCE}_${VM_NAME}" "sap-profiles"
        upper_vm=$(echo "${VM_NAME}" | tr '[:lower:]' '[:upper:]')
        if [ "$upper_vm" != "$VM_NAME" ]; then
            collect_file "/usr/sap/${SAP_SID}/SYS/profile/${SAP_SID}_D${APP_INSTANCE}_${upper_vm}" "sap-profiles"
        fi

        # App work directory logs
        mkdir -p "${COLLECT_DIR}/sap-work/app"
        for f in dev_w0 dev_w1 dev_w2 dev_disp dev_icm; do
            collect_file "/usr/sap/${SAP_SID}/D${APP_INSTANCE}/work/${f}" "sap-work/app"
        done
    fi
fi

# =========================================
# Role: sbd — SBD Fencing Node
# =========================================
if has_role "sbd"; then
    log "=== Collecting SBD node configs ==="
    mkdir -p "${COLLECT_DIR}/cluster"
    collect_file "/etc/sysconfig/sbd" "cluster"
    collect_file "/etc/corosync/corosync.conf" "cluster"

    if command -v sbd &>/dev/null; then
        {
            echo "=== SBD Device List ==="
            sbd -d /dev/disk/by-id/scsi-* list 2>/dev/null || echo "sbd list failed"
        } > "${COLLECT_DIR}/cluster/sbd-device-info.txt"
        log "  OK: sbd device info"
    fi
fi

# =========================================
# OS Configs — collected for ALL roles
# =========================================
log "=== Collecting OS configs ==="
mkdir -p "${COLLECT_DIR}/os"

collect_file "/etc/fstab" "os"
collect_file "/etc/hosts" "os"
collect_file "/etc/waagent.conf" "os"
collect_file "/etc/sysctl.conf" "os"
collect_file "/etc/resolv.conf" "os"
collect_file "/etc/chrony.conf" "os"
collect_file "/etc/ntp.conf" "os"
collect_file "/etc/multipath.conf" "os"

if [ -d "/etc/sysctl.d" ]; then
    mkdir -p "${COLLECT_DIR}/os/sysctl.d"
    find /etc/sysctl.d -name "*.conf" -exec cp {} "${COLLECT_DIR}/os/sysctl.d/" \;
    log "  OK: /etc/sysctl.d/*.conf"
fi

collect_file "/etc/security/limits.conf" "os"
if [ -d "/etc/security/limits.d" ]; then
    mkdir -p "${COLLECT_DIR}/os/limits.d"
    find /etc/security/limits.d -name "*.conf" -exec cp {} "${COLLECT_DIR}/os/limits.d/" \;
    log "  OK: /etc/security/limits.d/*.conf"
fi

sysctl -a > "${COLLECT_DIR}/os/sysctl-runtime.txt" 2>/dev/null || log "  WARN: sysctl -a failed"

{
    echo "=== THP enabled ==="
    cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo "not available"
    echo ""
    echo "=== THP defrag ==="
    cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || echo "not available"
} > "${COLLECT_DIR}/os/thp-status.txt"

{
    echo "=== Block Device Schedulers ==="
    for dev in /sys/block/sd*/queue/scheduler; do
        echo "$dev: $(cat "$dev" 2>/dev/null || echo 'N/A')"
    done
} > "${COLLECT_DIR}/os/io-scheduler.txt"

# udev rules (important for disk ordering in SAP)
if [ -d "/etc/udev/rules.d" ]; then
    mkdir -p "${COLLECT_DIR}/os/udev.d"
    find /etc/udev/rules.d -name "*.rules" -exec cp {} "${COLLECT_DIR}/os/udev.d/" \;
    log "  OK: /etc/udev/rules.d/*.rules"
fi

# System inventory (hardware, memory, disk layout, network)
log "Collecting system inventory..."
lscpu > "${COLLECT_DIR}/os/lscpu.txt" 2>/dev/null || log "  WARN: lscpu failed"
free -h > "${COLLECT_DIR}/os/memory.txt" 2>/dev/null || true
free -m > "${COLLECT_DIR}/os/free-m.txt" 2>/dev/null || true
df -hT > "${COLLECT_DIR}/os/disk-usage.txt" 2>/dev/null || true
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT > "${COLLECT_DIR}/os/lsblk.txt" 2>/dev/null || true
ip addr show > "${COLLECT_DIR}/os/ip-addr.txt" 2>/dev/null || true

# LVM stripe info (STAF checks DB-HANA-0034, DB-HANA-0036)
if command -v lvs &>/dev/null; then
    {
        echo "=== LVM Stripe Info ==="
        # For each HANA mount, find LV and its stripe size
        for mnt in /hana/data /hana/log /hana/shared /usr/sap; do
            dev=$(df --output=source "$mnt" 2>/dev/null | tail -1)
            if [ -n "$dev" ] && lvs "$dev" &>/dev/null; then
                stripe=$(lvs --segments --noheadings -o stripe_size "$dev" 2>/dev/null | head -1 | tr -d ' ')
                stripes=$(lvs --segments --noheadings -o stripes "$dev" 2>/dev/null | head -1 | tr -d ' ')
                echo "$mnt: device=$dev stripe_size=$stripe stripes=$stripes"
            else
                echo "$mnt: device=$dev stripe_size=N/A (not LVM or no stripes)"
            fi
        done
    } > "${COLLECT_DIR}/os/lvm-stripes.txt" 2>/dev/null || true
    log "  OK: LVM stripe info"
fi

# Time sync config
if command -v chronyc &>/dev/null; then
    chronyc tracking > "${COLLECT_DIR}/os/chrony-tracking.txt" 2>/dev/null || true
    log "  OK: chrony tracking"
elif command -v ntpq &>/dev/null; then
    ntpq -p > "${COLLECT_DIR}/os/ntp-peers.txt" 2>/dev/null || true
    log "  OK: ntp peers"
fi

# Tuning profile
if command -v tuned-adm &>/dev/null; then
    tuned-adm active > "${COLLECT_DIR}/os/tuned-profile.txt" 2>/dev/null || true
    log "  OK: tuned-adm active"
fi

# systemd DefaultTasksMax (Microsoft recommends 4096)
{
    echo "=== DefaultTasksMax ==="
    systemctl --no-pager show 2>/dev/null | grep DefaultTasksMax || echo "DefaultTasksMax=N/A"
} > "${COLLECT_DIR}/os/systemd-defaults.txt"
log "  OK: systemd defaults"

# fstrim.timer status (STAF check DB-HANA-0031 — should be disabled on HANA)
systemctl status fstrim.timer > "${COLLECT_DIR}/os/fstrim-status.txt" 2>/dev/null || echo "fstrim.timer: not found" > "${COLLECT_DIR}/os/fstrim-status.txt"
log "  OK: fstrim.timer status"

# Softdog config and module status (STAF checks SAP-0014, SAP-0015)
collect_file "/etc/modules-load.d/softdog.conf" "os"
lsmod | grep softdog > "${COLLECT_DIR}/os/lsmod-softdog.txt" 2>/dev/null || echo "softdog: not loaded" > "${COLLECT_DIR}/os/lsmod-softdog.txt"
log "  OK: softdog config/module"

# Azure Accelerated Networking check
{
    echo "=== Network Interface Driver ==="
    for iface in /sys/class/net/eth*; do
        name=$(basename "$iface")
        driver=$(ethtool -i "$name" 2>/dev/null | grep driver | awk '{print $2}')
        echo "$name: driver=$driver"
    done
} > "${COLLECT_DIR}/os/network-drivers.txt" 2>/dev/null || true
log "  OK: network drivers"

# =========================================
# Additional OS data for STAF coverage
# =========================================
log "Collecting additional STAF coverage data..."

# Kernel version (DB-HANA-0026-0029, IC-0008, IC-0030)
uname -r > "${COLLECT_DIR}/os/uname-r.txt" 2>/dev/null || true
log "  OK: uname -r"

# Sector/block size for HANA volumes (DB-HANA-0039, DB-HANA-0040)
{
    echo "/hana/data: $(stat -f -c '%s' /hana/data 2>/dev/null || echo 'N/A')"
    echo "/hana/log: $(stat -f -c '%s' /hana/log 2>/dev/null || echo 'N/A')"
} > "${COLLECT_DIR}/os/stat-block-size.txt" 2>/dev/null || true
log "  OK: stat block sizes"

# IMDS metadata (IC-0001-0005, IC-0009)
curl -s -H "Metadata:true" --connect-timeout 5 \
    "http://169.254.169.254/metadata/instance?api-version=2021-02-01" \
    > "${COLLECT_DIR}/os/imds-metadata.json" 2>/dev/null || echo '{}' > "${COLLECT_DIR}/os/imds-metadata.json"
log "  OK: IMDS metadata"

# OS release parsing (IC-0011, IC-0012)
collect_file "/etc/os-release" "os"
log "  OK: /etc/os-release"

# Timezone (IC-0013)
timedatectl 2>/dev/null > "${COLLECT_DIR}/os/timezone.txt" || date +%Z > "${COLLECT_DIR}/os/timezone.txt"
log "  OK: timezone"

# KDUMP config (IC-0041)
{
    echo "=== KDUMP Config ==="
    if [ -f /etc/sysconfig/kdump ]; then
        grep -v "^#" /etc/sysconfig/kdump | grep -v "^$"
    elif [ -f /etc/default/kdump-tools ]; then
        grep -v "^#" /etc/default/kdump-tools | grep -v "^$"
    else
        echo "kdump config not found"
    fi
    echo ""
    echo "=== KDUMP Service ==="
    systemctl is-active kdump 2>/dev/null || echo "unknown"
} > "${COLLECT_DIR}/os/kdump-config.txt" 2>/dev/null || true
log "  OK: kdump config"

# Installed packages — security endpoint checks (FC-0001 to FC-0015)
{
    if command -v rpm &>/dev/null; then
        rpm -qa 2>/dev/null | sort
    elif command -v dpkg &>/dev/null; then
        dpkg -l 2>/dev/null | awk '/^ii/{print $2"-"$3}'
    fi
} > "${COLLECT_DIR}/os/installed-packages.txt" 2>/dev/null || true
log "  OK: installed packages"

# Microsoft Defender health (FC-0008 to FC-0015)
if command -v mdatp &>/dev/null; then
    timeout 15 mdatp health > "${COLLECT_DIR}/os/mdatp-health.txt" 2>/dev/null || echo "mdatp health failed" > "${COLLECT_DIR}/os/mdatp-health.txt"
    log "  OK: mdatp health"
else
    echo "mdatp not installed" > "${COLLECT_DIR}/os/mdatp-health.txt"
    log "  SKIP: mdatp not installed"
fi

# =========================================
# Manifest
# =========================================
log "Creating manifest..."
{
    echo "{"
    echo "  \"hostname\": \"$(hostname)\","
    echo "  \"vm_name\": \"${VM_NAME}\","
    echo "  \"sap_sid\": \"${SAP_SID:-N/A}\","
    echo "  \"db_sid\": \"${DB_SID:-N/A}\","
    echo "  \"roles\": \"${ROLES}\","
    echo "  \"collected_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"os_release\": \"$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"')\","
    echo "  \"kernel\": \"$(uname -r)\","
    echo "  \"files\": ["
    first=true
    find "${COLLECT_DIR}" -type f ! -name "manifest.json" | sort | while read -r f; do
        relpath="${f#${COLLECT_DIR}/}"
        checksum=$(sha256sum "$f" | awk '{print $1}')
        size=$(stat -c %s "$f")
        if [ "$first" = true ]; then
            first=false
        else
            echo ","
        fi
        printf '    {"path": "%s", "sha256": "%s", "size": %s}' "$relpath" "$checksum" "$size"
    done
    echo ""
    echo "  ]"
    echo "}"
} > "${COLLECT_DIR}/manifest.json"

# =========================================
# Archive & Upload
# =========================================
log "Creating archive..."
tar -czf "${STAGING_DIR}/${ARCHIVE_NAME}" -C "/opt/sre/sap-configs/${BLOB_PREFIX}" latest
log "Archive: ${STAGING_DIR}/${ARCHIVE_NAME}"

log "Authenticating with UMI (${UMI_CLIENT_ID})..."
az login --identity --username "$UMI_CLIENT_ID" --output none 2>/dev/null
if [ $? -ne 0 ]; then
    log "ERROR: az login --identity failed for UMI ${UMI_CLIENT_ID}"
    exit 1
fi

# Upload archive to dated directory
log "Uploading archive to ${CONTAINER}/${BLOB_PREFIX}/${DATE_DIR}/..."
az storage blob upload \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$CONTAINER" \
    --name "${BLOB_PREFIX}/${DATE_DIR}/${ARCHIVE_NAME}" \
    --file "${STAGING_DIR}/${ARCHIVE_NAME}" \
    --auth-mode login \
    --output none 2>/dev/null || log "WARN: archive upload failed (may be network rules)"

# Upload individual files to latest/
log "Uploading files to ${CONTAINER}/${BLOB_PREFIX}/latest/..."
az storage blob upload-batch \
    --account-name "$STORAGE_ACCOUNT" \
    --destination "$CONTAINER" \
    --source "${COLLECT_DIR}" \
    --destination-path "${BLOB_PREFIX}/latest" \
    --auth-mode login \
    --output none 2>/dev/null || log "WARN: batch upload failed (may be network rules or old az cli)"

log "Upload complete."

# Cleanup staging
rm -rf "${STAGING_DIR}"
log "Staging cleaned up. Latest configs retained at: /opt/sre/sap-configs/${BLOB_PREFIX}/latest/"

log "========================================="
log "Collection complete. Files at:"
log "  Archive: ${CONTAINER}/${BLOB_PREFIX}/${DATE_DIR}/${ARCHIVE_NAME}"
log "  Latest:  ${CONTAINER}/${BLOB_PREFIX}/latest/"
log "========================================="
