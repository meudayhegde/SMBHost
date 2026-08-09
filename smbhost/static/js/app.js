/**
 * SMBHost Web UI — Application logic v2
 *
 * Alpine.js reactive UI with:
 * - Loading states & skeleton screens
 * - Confirmation dialogs (replaces browser confirm())
 * - Copy-to-clipboard for SMB connection strings
 * - Drive search/filter
 * - Improved toast notifications with icons
 * - Proper settings persistence
 * - WebSocket reconnection with backoff
 */

function smbhostApp() {
    return {
        // ── Tab State ──────────────────────────────────────────────
        currentTab: 'dashboard',
        tabs: [
            { id: 'dashboard', label: 'Dashboard' },
            { id: 'drives', label: 'Drives' },
            { id: 'settings', label: 'Settings' },
            { id: 'system', label: 'System' },
        ],

        // ── Data ───────────────────────────────────────────────────
        drives: [],
        shares: [],
        driveSearch: '',

        status: {
            version: '',
            uptime: '',
            samba_running: false,
            nmbd_status: '',
            python_version: '',
            hostname: '',
            server_ip: '',
        },

        settings: {
            workgroup: 'WORKGROUP',
            netbios_name: 'SMBHOST',
            server_string: 'SMBHost File Server',
            web_bind: '127.0.0.1',
            web_port: 8080,
        },
        settingsLoading: false,
        settingsSaved: false,
        settingsError: '',

        // ── Loading States ─────────────────────────────────────────
        loading: {
            drives: false,
            shares: false,
            status: false,
            logs: false,
            initial: true,
        },

        // ── Share Modal ────────────────────────────────────────────
        showModal: false,
        modalTitle: 'Create Share',
        modalSubmitLabel: 'Create Share',
        modalMode: 'create',
        modalLoading: false,
        editingDriveUuid: null,

        shareForm: {
            drive_uuid: '',
            drive_label: '',
            share_name: '',
            mount_point: '',
            auth_mode: 'guest',
            username: '',
            password: '',
            read_only: false,
            browseable: true,
        },

        // ── Confirm Dialog ─────────────────────────────────────────
        confirmDialog: {
            show: false,
            title: '',
            message: '',
            action: null,
            loading: false,
        },

        // ── Logs ───────────────────────────────────────────────────
        logs: '',

        // ── Samba Test ─────────────────────────────────────────────
        sambaTestOutput: null,
        sambaTestValid: false,
        sambaTestLoading: false,

        // ── Toasts ─────────────────────────────────────────────────
        toasts: [],

        // ── WebSocket ──────────────────────────────────────────────
        _ws: null,
        _wsReconnectTimer: null,
        _wsReconnectDelay: 2000,

        // ── Computed ───────────────────────────────────────────────
        get filteredDrives() {
            if (!this.driveSearch.trim()) return this.drives;
            const q = this.driveSearch.toLowerCase();
            return this.drives.filter(d =>
                (d.label || '').toLowerCase().includes(q) ||
                d.uuid.toLowerCase().includes(q) ||
                (d.device_path || '').toLowerCase().includes(q) ||
                (d.fstype || '').toLowerCase().includes(q)
            );
        },

        get connectedDrivesCount() {
            return this.drives.filter(d => d.is_mounted).length;
        },

        get activeSharesCount() {
            return this.shares.filter(s => s.enabled).length;
        },

        get hasAnyDrives() { return this.drives.length > 0; },
        get hasAnyShares() { return this.shares.length > 0; },

        // ── Init ───────────────────────────────────────────────────
        async init() {
            await Promise.all([
                this.refreshDrives(),
                this.refreshShares(),
                this.refreshStatus(),
                this.loadSettings(),
            ]);
            this.loading.initial = false;
            this.connectWebSocket();

            // Poll every 30s
            setInterval(() => {
                this.refreshDrives();
                this.refreshShares();
                this.refreshStatus();
            }, 30000);
        },

        // ── API Helpers ────────────────────────────────────────────
        async apiGet(url) {
            const resp = await fetch(url);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            return resp.json();
        },

        async apiPost(url, body) {
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            return resp.json();
        },

        async apiPut(url, body) {
            const resp = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            return resp.json();
        },

        async apiDelete(url) {
            const resp = await fetch(url, { method: 'DELETE' });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            return resp.json();
        },

        // ── Toast ──────────────────────────────────────────────────
        addToast(message, type = 'success') {
            const id = Date.now() + Math.random();
            this.toasts.push({ id, message, type, leaving: false });

            // Auto-dismiss after 5s
            setTimeout(() => this.dismissToast(id), 5000);
        },

        dismissToast(id) {
            const idx = this.toasts.findIndex(t => t.id === id);
            if (idx === -1) return;
            // Trigger leave animation
            this.toasts[idx].leaving = true;
            setTimeout(() => {
                this.toasts = this.toasts.filter(t => t.id !== id);
            }, 250);
        },

        // ── Confirm Dialog ─────────────────────────────────────────
        showConfirm(title, message, action) {
            this.confirmDialog = {
                show: true,
                title,
                message,
                action,
                loading: false,
            };
        },

        async executeConfirm() {
            this.confirmDialog.loading = true;
            try {
                await this.confirmDialog.action();
            } catch (e) {
                this.addToast('Error: ' + e.message, 'error');
            } finally {
                this.confirmDialog.show = false;
                this.confirmDialog.loading = false;
            }
        },

        cancelConfirm() {
            this.confirmDialog.show = false;
        },

        // ── Copy Helper ────────────────────────────────────────────
        async copyToClipboard(text) {
            try {
                await navigator.clipboard.writeText(text);
                this.addToast('Copied to clipboard');
            } catch (e) {
                // Fallback
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                this.addToast('Copied to clipboard');
            }
        },

        // ── SMB Connection Strings ─────────────────────────────────
        getSmbUrl(shareName) {
            const host = this.status.hostname || this.settings.netbios_name.toLowerCase() || 'smbhost';
            return `smb://${host}/${shareName}`;
        },

        getSmbUrlIp(shareName) {
            const ip = this.status.server_ip || window.location.hostname;
            return `smb://${ip}/${shareName}`;
        },

        // ── WebSocket ──────────────────────────────────────────────
        connectWebSocket() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${location.host}/api/drives/events`;

            try {
                this._ws = new WebSocket(wsUrl);
                this._ws.onmessage = (event) => {
                    try {
                        const msg = JSON.parse(event.data);
                        if (msg.event === 'drive_added' || msg.event === 'drive_removed') {
                            this.refreshDrives();
                            this.refreshShares();
                            const action = msg.event === 'drive_added' ? 'connected' : 'disconnected';
                            this.addToast(`Drive ${action}: ${msg.data?.label || 'Unknown'}`, 'info');
                        }
                    } catch (e) {
                        // Ignore parse errors
                    }
                };
                this._ws.onopen = () => {
                    this._wsReconnectDelay = 2000; // Reset backoff on success
                };
                this._ws.onclose = () => {
                    this._wsReconnectTimer = setTimeout(() => {
                        this.connectWebSocket();
                        this._wsReconnectDelay = Math.min(this._wsReconnectDelay * 1.5, 30000);
                    }, this._wsReconnectDelay);
                };
                this._ws.onerror = () => {
                    this._ws?.close();
                };
            } catch (e) {
                // WebSocket not supported
            }
        },

        // ── Data Refresh ───────────────────────────────────────────
        async refreshDrives() {
            this.loading.drives = true;
            try {
                const data = await this.apiGet('/api/drives');
                this.drives = data.drives || [];
            } catch (e) {
                console.error('Failed to fetch drives:', e);
            } finally {
                this.loading.drives = false;
            }
        },

        async refreshShares() {
            this.loading.shares = true;
            try {
                const data = await this.apiGet('/api/shares');
                this.shares = data.shares || [];
            } catch (e) {
                console.error('Failed to fetch shares:', e);
            } finally {
                this.loading.shares = false;
            }
        },

        async refreshStatus() {
            this.loading.status = true;
            try {
                const data = await this.apiGet('/api/system/status');
                this.status = {
                    version: data.app?.version || '',
                    uptime: data.app?.uptime || '',
                    samba_running: data.samba?.running || false,
                    nmbd_status: data.samba?.nmbd || 'unknown',
                    python_version: data.app?.python || '',
                    hostname: data.host?.hostname || '',
                    server_ip: data.host?.ip || '',
                };
            } catch (e) {
                console.error('Failed to fetch status:', e);
            } finally {
                this.loading.status = false;
            }
        },

        async refreshLogs() {
            this.loading.logs = true;
            try {
                const data = await this.apiGet('/api/system/logs?lines=50');
                this.logs = data.logs || '';
            } catch (e) {
                this.logs = 'Failed to load logs.';
            } finally {
                this.loading.logs = false;
            }
        },

        async loadSettings() {
            this.settingsLoading = true;
            try {
                const data = await this.apiGet('/api/system/config');
                this.settings.workgroup = data.workgroup || 'WORKGROUP';
                this.settings.netbios_name = data.netbios_name || 'SMBHOST';
                this.settings.server_string = data.server_string || 'SMBHost File Server';
                this.settings.web_bind = data.web_bind || '127.0.0.1';
                this.settings.web_port = data.web_port || 8080;
            } catch (e) {
                // Use defaults
            } finally {
                this.settingsLoading = false;
            }
        },

        // ── Share Modal ────────────────────────────────────────────
        openCreateShare(drive) {
            this.modalMode = 'create';
            this.modalTitle = 'Create Share';
            this.modalSubmitLabel = 'Create Share';
            this.editingDriveUuid = null;
            this.shareForm = {
                drive_uuid: drive.uuid,
                drive_label: drive.label || '',
                share_name: (drive.label || 'drive_' + drive.uuid.substring(0, 8)).substring(0, 15),
                mount_point: drive.mount_point || '',
                auth_mode: 'guest',
                username: '',
                password: '',
                read_only: false,
                browseable: true,
            };
            this.showModal = true;
        },

        editShare(drive) {
            const share = drive.share;
            if (!share) return;

            this.modalMode = 'edit';
            this.modalTitle = 'Edit Share';
            this.modalSubmitLabel = 'Update Share';
            this.editingDriveUuid = drive.uuid;
            this.shareForm = {
                drive_uuid: drive.uuid,
                drive_label: drive.label || '',
                share_name: share.share_name || '',
                mount_point: drive.mount_point || '',
                auth_mode: share.auth_mode || 'guest',
                username: share.username || '',
                password: '',
                read_only: share.read_only || false,
                browseable: share.browseable !== false,
            };
            this.showModal = true;
        },

        async submitShare() {
            this.modalLoading = true;
            try {
                if (this.modalMode === 'create') {
                    await this.apiPost('/api/shares', this.shareForm);
                    this.addToast('Share created successfully');
                } else if (this.modalMode === 'edit' && this.editingDriveUuid) {
                    const updates = {};
                    if (this.shareForm.share_name) updates.share_name = this.shareForm.share_name;
                    if (this.shareForm.auth_mode) updates.auth_mode = this.shareForm.auth_mode;
                    if (this.shareForm.username) updates.username = this.shareForm.username;
                    if (this.shareForm.password) updates.password = this.shareForm.password;
                    updates.read_only = this.shareForm.read_only;
                    updates.browseable = this.shareForm.browseable;

                    await this.apiPut('/api/shares/' + this.editingDriveUuid, updates);
                    this.addToast('Share updated successfully');
                }

                this.showModal = false;
                await Promise.all([this.refreshDrives(), this.refreshShares()]);
            } catch (e) {
                this.addToast('Error: ' + e.message, 'error');
            } finally {
                this.modalLoading = false;
            }
        },

        confirmDeleteShare(drive) {
            const name = drive.label || drive.uuid.substring(0, 8);
            const shareName = drive.share?.share_name || name;
            this.showConfirm(
                'Remove Share',
                `Remove the SMB share "${shareName}"? The drive data will not be affected.`,
                () => this.deleteShare(drive.uuid)
            );
        },

        async deleteShare(driveUuid) {
            await this.apiDelete('/api/shares/' + driveUuid);
            this.addToast('Share removed');
            await Promise.all([this.refreshDrives(), this.refreshShares()]);
        },

        // ── Settings ───────────────────────────────────────────────
        async saveSettings() {
            this.settingsLoading = true;
            this.settingsSaved = false;
            this.settingsError = '';
            try {
                await this.apiPut('/api/system/config', this.settings);
                this.settingsSaved = true;
                this.addToast('Settings saved');
                setTimeout(() => { this.settingsSaved = false; }, 4000);
            } catch (e) {
                this.settingsError = e.message;
                this.addToast('Failed to save settings: ' + e.message, 'error');
            } finally {
                this.settingsLoading = false;
            }
        },

        // ── System Actions ─────────────────────────────────────────
        confirmReloadSamba() {
            this.showConfirm(
                'Reload Samba',
                'Reload the Samba configuration. Active connections will not be interrupted.',
                () => this.reloadSamba()
            );
        },

        async reloadSamba() {
            await this.apiPost('/api/system/samba/reload', {});
            this.addToast('Samba configuration reloaded');
            await this.refreshStatus();
        },

        confirmRestartSamba() {
            this.showConfirm(
                'Restart Samba',
                'Restart Samba services (smbd + nmbd). Active file transfers will be interrupted!',
                () => this.restartSamba()
            );
        },

        async restartSamba() {
            await this.apiPost('/api/system/samba/restart', {});
            this.addToast('Samba services restarted');
            await this.refreshStatus();
        },

        async testSambaConfig() {
            this.sambaTestLoading = true;
            try {
                const data = await this.apiGet('/api/system/samba/test');
                this.sambaTestOutput = data.output;
                this.sambaTestValid = data.valid;
                if (data.valid) {
                    this.addToast('Samba configuration is valid');
                }
            } catch (e) {
                this.sambaTestOutput = 'Error: ' + e.message;
                this.sambaTestValid = false;
                this.addToast('Samba test failed: ' + e.message, 'error');
            } finally {
                this.sambaTestLoading = false;
            }
        },
    };
}
