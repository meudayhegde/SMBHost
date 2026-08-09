/**
 * SMBHost Web UI — Application logic
 *
 * Uses Alpine.js for reactive UI. This file provides the
 * smbhostApp() Alpine component with all data and methods.
 */

function smbhostApp() {
    return {
        // ── State ──────────────────────────────────────────────────
        currentTab: 'dashboard',
        tabs: [
            { id: 'dashboard', label: 'Dashboard' },
            { id: 'drives', label: 'Drives' },
            { id: 'settings', label: 'Settings' },
            { id: 'system', label: 'System' },
        ],

        drives: [],
        shares: [],

        status: {
            version: '',
            uptime: '',
            samba_running: false,
            nmbd_status: '',
            python_version: '',
            hostname: '',
        },

        settings: {
            workgroup: 'WORKGROUP',
            netbios_name: 'SMBHOST',
            server_string: 'SMBHost File Server',
            web_bind: '127.0.0.1',
            web_port: 8080,
        },
        settingsSaved: false,
        settingsError: '',

        // Modal state
        showModal: false,
        modalTitle: 'Create Share',
        modalSubmitLabel: 'Create Share',
        modalMode: 'create', // 'create' | 'edit'
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

        // Logs
        logs: '',

        // Samba test
        sambaTestOutput: null,
        sambaTestValid: false,

        // Toast
        toast: {
            show: false,
            message: '',
            type: 'success',
        },

        // WebSocket
        _ws: null,
        _wsReconnectTimer: null,

        // ── Init ───────────────────────────────────────────────────
        async init() {
            await Promise.all([
                this.refreshDrives(),
                this.refreshShares(),
                this.refreshStatus(),
                this.loadSettings(),
            ]);
            this.connectWebSocket();

            // Poll for updates every 30s
            setInterval(() => {
                this.refreshDrives();
                this.refreshShares();
                this.refreshStatus();
            }, 30000);
        },

        // ── API Helpers ────────────────────────────────────────────
        async apiGet(url) {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
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
        showToast(message, type = 'success') {
            this.toast = { show: true, message, type };
            setTimeout(() => { this.toast.show = false; }, 4000);
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
                        }
                    } catch (e) {
                        // Ignore parse errors
                    }
                };
                this._ws.onclose = () => {
                    // Reconnect after 5 seconds
                    this._wsReconnectTimer = setTimeout(() => this.connectWebSocket(), 5000);
                };
                this._ws.onerror = () => {
                    this._ws?.close();
                };
            } catch (e) {
                // WebSocket not supported or connection failed
            }
        },

        // ── Data Refresh ───────────────────────────────────────────
        async refreshDrives() {
            try {
                const data = await this.apiGet('/api/drives');
                this.drives = data.drives || [];
            } catch (e) {
                console.error('Failed to fetch drives:', e);
            }
        },

        async refreshShares() {
            try {
                const data = await this.apiGet('/api/shares');
                this.shares = data.shares || [];
            } catch (e) {
                console.error('Failed to fetch shares:', e);
            }
        },

        async refreshStatus() {
            try {
                const data = await this.apiGet('/api/system/status');
                this.status = {
                    version: data.app?.version || '',
                    uptime: data.app?.uptime || '',
                    samba_running: data.samba?.running || false,
                    nmbd_status: data.samba?.nmbd || '',
                    python_version: data.app?.python || '',
                    hostname: data.host?.hostname || '',
                };
            } catch (e) {
                console.error('Failed to fetch status:', e);
            }
        },

        async refreshLogs() {
            try {
                const data = await this.apiGet('/api/system/logs?lines=50');
                this.logs = data.logs || '';
            } catch (e) {
                this.logs = 'Failed to load logs.';
            }
        },

        async loadSettings() {
            // Settings are loaded from the drives API which includes global config
            // For now, use defaults that will be overwritten by the server
            try {
                const data = await this.apiGet('/api/system/status');
                // Settings would come from a dedicated endpoint in a production app
            } catch (e) {
                // Use defaults
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
            try {
                if (this.modalMode === 'create') {
                    await this.apiPost('/api/shares', this.shareForm);
                    this.showToast('Share created successfully');
                } else if (this.modalMode === 'edit' && this.editingDriveUuid) {
                    // Only send changed fields
                    const updates = {};
                    if (this.shareForm.share_name) updates.share_name = this.shareForm.share_name;
                    if (this.shareForm.auth_mode) updates.auth_mode = this.shareForm.auth_mode;
                    if (this.shareForm.username) updates.username = this.shareForm.username;
                    if (this.shareForm.password) updates.password = this.shareForm.password;
                    updates.read_only = this.shareForm.read_only;
                    updates.browseable = this.shareForm.browseable;

                    await this.apiPut('/api/shares/' + this.editingDriveUuid, updates);
                    this.showToast('Share updated successfully');
                }

                this.showModal = false;
                await Promise.all([this.refreshDrives(), this.refreshShares()]);
            } catch (e) {
                this.showToast('Error: ' + e.message, 'error');
            }
        },

        async deleteShare(drive) {
            if (!confirm(`Remove share for "${drive.label || drive.uuid.substring(0, 8)}"?`)) return;

            try {
                await this.apiDelete('/api/shares/' + drive.uuid);
                this.showToast('Share removed');
                await Promise.all([this.refreshDrives(), this.refreshShares()]);
            } catch (e) {
                this.showToast('Error: ' + e.message, 'error');
            }
        },

        // ── Settings ───────────────────────────────────────────────
        async saveSettings() {
            this.settingsSaved = false;
            this.settingsError = '';
            try {
                // In a production app, this would hit a settings API endpoint
                // For now, settings are applied via the config file
                this.settingsSaved = true;
                setTimeout(() => { this.settingsSaved = false; }, 3000);
            } catch (e) {
                this.settingsError = e.message;
            }
        },

        // ── System Actions ─────────────────────────────────────────
        async reloadSamba() {
            try {
                await this.apiPost('/api/system/samba/reload', {});
                this.showToast('Samba configuration reloaded');
            } catch (e) {
                this.showToast('Error: ' + e.message, 'error');
            }
        },

        async restartSamba() {
            if (!confirm('Restart Samba services? Active connections will be interrupted.')) return;
            try {
                await this.apiPost('/api/system/samba/restart', {});
                this.showToast('Samba services restarted');
                await this.refreshStatus();
            } catch (e) {
                this.showToast('Error: ' + e.message, 'error');
            }
        },

        async testSambaConfig() {
            try {
                const data = await this.apiGet('/api/system/samba/test');
                this.sambaTestOutput = data.output;
                this.sambaTestValid = data.valid;
            } catch (e) {
                this.sambaTestOutput = 'Error: ' + e.message;
                this.sambaTestValid = false;
            }
        },
    };
}
