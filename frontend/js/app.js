function app() {
    return {
        // State
        activeTab: 'csv',
        loading: false,
        dragover: false,
        
        // Connection status
        ynabConnected: false,
        akahuConnected: false,
        
        // CSV Import
        csvFile: null,
        csvColumns: [],
        csvPreview: [],
        selectedProfile: '',
        bankProfiles: {},
        mapping: {
            date: '',
            amount: '',
            payee: '',
            memo: '',
            dateFormat: '%d/%m/%Y',
            skipRows: 0,
            amountInverted: false
        },
        
        // YNAB
        budgets: [],
        accounts: [],
        selectedBudget: '',
        selectedAccount: '',
        
        // Transactions
        transactions: [],
        
        // Akahu
        akahuAccounts: [],
        
        // Link Modal
        showLinkModal: false,
        linkingAccount: null,
        linkBudget: '',
        linkAccount: '',
        linkAccounts: [],
        
        // Schedule Modal
        showScheduleModal: false,
        schedulingAccount: null,
        scheduleConfig: {
            enabled: true,
            interval_hours: 6,
            days_to_sync: 7
        },
        
        // Sync Logs
        syncLogs: [],
        
        // History
        history: [],
        stats: {},
        
        // Saved Profiles
        savedProfiles: [],
        
        // Toast notifications
        toasts: [],
        toastId: 0,
        
        // Initialize
        async init() {
            await this.testConnections();
            await this.loadBankProfiles();
            await this.loadBudgets();
            await this.loadSavedProfiles();
            
            if (this.akahuConnected) {
                await this.loadAkahuAccounts();
            }
            
            await this.loadHistory();
            await this.loadStats();
        },
        
        // Test API connections
        async testConnections() {
            try {
                const ynabRes = await fetch('/api/ynab/test');
                this.ynabConnected = ynabRes.ok;
            } catch {
                this.ynabConnected = false;
            }
            
            try {
                const akahuRes = await fetch('/api/akahu/test');
                this.akahuConnected = akahuRes.ok;
            } catch {
                this.akahuConnected = false;
            }
        },
        
        // Load bank profiles
        async loadBankProfiles() {
            try {
                const res = await fetch('/api/csv/profiles');
                if (res.ok) {
                    this.bankProfiles = await res.json();
                }
            } catch (e) {
                console.error('Failed to load bank profiles:', e);
            }
        },
        
        // Load YNAB budgets
        async loadBudgets() {
            if (!this.ynabConnected) return;
            
            try {
                const res = await fetch('/api/ynab/budgets');
                if (res.ok) {
                    this.budgets = await res.json();
                }
            } catch (e) {
                console.error('Failed to load budgets:', e);
            }
        },
        
        // Load YNAB accounts for selected budget
        async loadAccounts() {
            if (!this.selectedBudget) {
                this.accounts = [];
                return;
            }
            
            try {
                const res = await fetch(`/api/ynab/budgets/${this.selectedBudget}/accounts`);
                if (res.ok) {
                    this.accounts = await res.json();
                }
            } catch (e) {
                console.error('Failed to load accounts:', e);
            }
        },
        
        // Load saved mapping profiles
        async loadSavedProfiles() {
            try {
                const res = await fetch('/api/mappings/');
                if (res.ok) {
                    this.savedProfiles = await res.json();
                }
            } catch (e) {
                console.error('Failed to load saved profiles:', e);
            }
        },
        
        // Handle file selection
        async handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) {
                await this.processFile(file);
            }
        },
        
        // Handle file drop
        async handleFileDrop(event) {
            this.dragover = false;
            const file = event.dataTransfer.files[0];
            if (file && file.name.endsWith('.csv')) {
                await this.processFile(file);
            }
        },
        
        // Process uploaded file
        async processFile(file) {
            this.csvFile = file;
            this.transactions = [];
            
            // Detect columns
            const formData = new FormData();
            formData.append('file', file);
            
            try {
                const res = await fetch('/api/csv/detect-columns', {
                    method: 'POST',
                    body: formData
                });
                
                if (res.ok) {
                    const data = await res.json();
                    this.csvColumns = data.columns;
                    this.csvPreview = data.preview;
                    
                    // Try to auto-detect mappings
                    this.autoDetectMappings();
                }
            } catch (e) {
                this.showToast('Failed to process file', 'error');
            }
        },
        
        // Auto-detect column mappings
        autoDetectMappings() {
            const columns = this.csvColumns.map(c => c.toLowerCase());
            
            // Date
            const dateIndex = columns.findIndex(c => 
                c.includes('date') || c.includes('datum')
            );
            if (dateIndex >= 0) this.mapping.date = this.csvColumns[dateIndex];
            
            // Amount
            const amountIndex = columns.findIndex(c => 
                c.includes('amount') || c.includes('value') || c.includes('sum')
            );
            if (amountIndex >= 0) this.mapping.amount = this.csvColumns[amountIndex];
            
            // Payee
            const payeeIndex = columns.findIndex(c => 
                c.includes('payee') || c.includes('description') || c.includes('merchant') || c.includes('other party')
            );
            if (payeeIndex >= 0) this.mapping.payee = this.csvColumns[payeeIndex];
            
            // Memo
            const memoIndex = columns.findIndex(c => 
                c.includes('memo') || c.includes('reference') || c.includes('particulars')
            );
            if (memoIndex >= 0) this.mapping.memo = this.csvColumns[memoIndex];
        },
        
        // Apply bank profile
        applyProfile() {
            if (!this.selectedProfile) return;
            
            const profile = this.bankProfiles[this.selectedProfile];
            if (profile) {
                this.mapping.date = profile.column_mappings.date;
                this.mapping.amount = profile.column_mappings.amount;
                this.mapping.payee = profile.column_mappings.payee || '';
                this.mapping.memo = profile.column_mappings.memo || '';
                this.mapping.dateFormat = profile.date_format;
                this.mapping.skipRows = profile.skip_rows;
                this.mapping.amountInverted = profile.amount_inverted;
            }
        },
        
        // Parse CSV
        async parseCSV() {
            if (!this.csvFile) return;
            
            this.loading = true;
            
            const formData = new FormData();
            formData.append('file', this.csvFile);
            formData.append('date_column', this.mapping.date);
            formData.append('amount_column', this.mapping.amount);
            formData.append('payee_column', this.mapping.payee || '');
            formData.append('memo_column', this.mapping.memo || '');
            formData.append('date_format', this.mapping.dateFormat);
            formData.append('amount_inverted', this.mapping.amountInverted);
            formData.append('skip_rows', this.mapping.skipRows);
            
            try {
                const res = await fetch('/api/csv/parse', {
                    method: 'POST',
                    body: formData
                });
                
                if (res.ok) {
                    this.transactions = await res.json();
                    this.showToast(`Parsed ${this.transactions.length} transactions`, 'success');
                } else {
                    const error = await res.json();
                    this.showToast(error.detail || 'Failed to parse CSV', 'error');
                }
            } catch (e) {
                this.showToast('Failed to parse CSV', 'error');
            } finally {
                this.loading = false;
            }
        },
        
        // Import transactions to YNAB
        async importTransactions() {
            if (this.transactions.length === 0) return;
            
            this.loading = true;
            
            const newTransactions = this.transactions.filter(t => !t.is_duplicate);
            
            try {
                const res = await fetch(`/api/csv/import?ynab_budget_id=${this.selectedBudget}&ynab_account_id=${this.selectedAccount}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(newTransactions)
                });
                
                if (res.ok) {
                    const result = await res.json();
                    this.showToast(`Imported ${result.imported} transactions`, 'success');
                    this.transactions = [];
                    this.csvFile = null;
                    this.csvColumns = [];
                    await this.loadHistory();
                    await this.loadStats();
                } else {
                    const error = await res.json();
                    this.showToast(error.detail || 'Failed to import', 'error');
                }
            } catch (e) {
                this.showToast('Failed to import transactions', 'error');
            } finally {
                this.loading = false;
            }
        },
        
        // Akahu
        async loadAkahuAccounts() {
            try {
                const res = await fetch('/api/akahu/accounts');
                if (res.ok) {
                    this.akahuAccounts = await res.json();
                }
            } catch (e) {
                console.error('Failed to load Akahu accounts:', e);
            }
        },
        
        openLinkModal(account) {
            this.linkingAccount = account;
            this.linkBudget = '';
            this.linkAccount = '';
            this.linkAccounts = [];
            this.showLinkModal = true;
        },
        
        async loadLinkAccounts() {
            if (!this.linkBudget) {
                this.linkAccounts = [];
                return;
            }
            
            try {
                const res = await fetch(`/api/ynab/budgets/${this.linkBudget}/accounts`);
                if (res.ok) {
                    this.linkAccounts = await res.json();
                }
            } catch (e) {
                console.error('Failed to load accounts:', e);
            }
        },
        
        async linkAkahuAccount() {
            if (!this.linkingAccount || !this.linkBudget || !this.linkAccount) return;
            
            try {
                const res = await fetch('/api/akahu/accounts/link', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        akahu_account_id: this.linkingAccount.id,
                        ynab_budget_id: this.linkBudget,
                        ynab_account_id: this.linkAccount,
                        auto_sync: false
                    })
                });
                
                if (res.ok) {
                    this.showToast('Account linked successfully', 'success');
                    this.showLinkModal = false;
                    await this.loadAkahuAccounts();
                } else {
                    this.showToast('Failed to link account', 'error');
                }
            } catch (e) {
                this.showToast('Failed to link account', 'error');
            }
        },
        
        async syncAkahuAccount(accountId) {
            this.loading = true;
            
            try {
                const res = await fetch(`/api/akahu/sync/${accountId}?days=30`, {
                    method: 'POST'
                });
                
                if (res.ok) {
                    const result = await res.json();
                    this.showToast(`Synced ${result.imported} transactions`, 'success');
                    await this.loadAkahuAccounts();
                    await this.loadHistory();
                    await this.loadStats();
                } else {
                    const error = await res.json();
                    this.showToast(error.detail || 'Failed to sync', 'error');
                }
            } catch (e) {
                this.showToast('Failed to sync account', 'error');
            } finally {
                this.loading = false;
            }
        },
        
        // Schedule methods
        openScheduleModal(account) {
            this.schedulingAccount = account;
            this.scheduleConfig = {
                enabled: account.schedule_enabled || false,
                interval_hours: account.schedule_interval_hours || 6,
                days_to_sync: account.schedule_days_to_sync || 7
            };
            this.showScheduleModal = true;
        },
        
        async saveSchedule() {
            if (!this.schedulingAccount) return;
            
            this.loading = true;
            
            try {
                const res = await fetch(`/api/akahu/accounts/${this.schedulingAccount.id}/schedule`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.scheduleConfig)
                });
                
                if (res.ok) {
                    this.showToast(
                        this.scheduleConfig.enabled 
                            ? `Schedule enabled: sync every ${this.scheduleConfig.interval_hours} hours`
                            : 'Schedule disabled',
                        'success'
                    );
                    this.showScheduleModal = false;
                    await this.loadAkahuAccounts();
                } else {
                    const error = await res.json();
                    this.showToast(error.detail || 'Failed to save schedule', 'error');
                }
            } catch (e) {
                this.showToast('Failed to save schedule', 'error');
            } finally {
                this.loading = false;
            }
        },
        
        async disableSchedule(accountId) {
            this.loading = true;
            
            try {
                const res = await fetch(`/api/akahu/accounts/${accountId}/schedule`, {
                    method: 'DELETE'
                });
                
                if (res.ok) {
                    this.showToast('Schedule disabled', 'success');
                    await this.loadAkahuAccounts();
                } else {
                    this.showToast('Failed to disable schedule', 'error');
                }
            } catch (e) {
                this.showToast('Failed to disable schedule', 'error');
            } finally {
                this.loading = false;
            }
        },
        
        async loadSyncLogs(accountId = null) {
            try {
                const url = accountId 
                    ? `/api/akahu/sync-logs?akahu_account_id=${accountId}&limit=20`
                    : '/api/akahu/sync-logs?limit=50';
                const res = await fetch(url);
                if (res.ok) {
                    this.syncLogs = await res.json();
                }
            } catch (e) {
                console.error('Failed to load sync logs:', e);
            }
        },
        
        getStatusColor(status) {
            switch (status) {
                case 'success': return 'text-green-600';
                case 'failed': return 'text-red-600';
                case 'running': return 'text-blue-600';
                default: return 'text-gray-600';
            }
        },
        
        getStatusBg(status) {
            switch (status) {
                case 'success': return 'bg-green-100';
                case 'failed': return 'bg-red-100';
                case 'running': return 'bg-blue-100';
                default: return 'bg-gray-100';
            }
        },
        
        // History
        async loadHistory() {
            try {
                const res = await fetch('/api/ynab/history?limit=100');
                if (res.ok) {
                    this.history = await res.json();
                }
            } catch (e) {
                console.error('Failed to load history:', e);
            }
        },
        
        async loadStats() {
            try {
                const res = await fetch('/api/ynab/stats');
                if (res.ok) {
                    this.stats = await res.json();
                }
            } catch (e) {
                console.error('Failed to load stats:', e);
            }
        },
        
        // Settings
        async testYNAB() {
            try {
                const res = await fetch('/api/ynab/test');
                if (res.ok) {
                    this.ynabConnected = true;
                    this.showToast('YNAB connection successful', 'success');
                } else {
                    this.ynabConnected = false;
                    this.showToast('YNAB connection failed', 'error');
                }
            } catch (e) {
                this.ynabConnected = false;
                this.showToast('YNAB connection failed', 'error');
            }
        },
        
        async testAkahu() {
            try {
                const res = await fetch('/api/akahu/test');
                if (res.ok) {
                    this.akahuConnected = true;
                    this.showToast('Akahu connection successful', 'success');
                } else {
                    this.akahuConnected = false;
                    this.showToast('Akahu connection failed', 'error');
                }
            } catch (e) {
                this.akahuConnected = false;
                this.showToast('Akahu connection failed', 'error');
            }
        },
        
        async deleteProfile(profileId) {
            if (!confirm('Delete this profile?')) return;
            
            try {
                const res = await fetch(`/api/mappings/${profileId}`, {
                    method: 'DELETE'
                });
                
                if (res.ok) {
                    this.showToast('Profile deleted', 'success');
                    await this.loadSavedProfiles();
                }
            } catch (e) {
                this.showToast('Failed to delete profile', 'error');
            }
        },
        
        // Utilities
        formatDate(dateStr) {
            if (!dateStr) return '-';
            const date = new Date(dateStr);
            return date.toLocaleDateString('en-NZ');
        },
        
        formatDateTime(dateStr) {
            if (!dateStr) return '-';
            const date = new Date(dateStr);
            return date.toLocaleString('en-NZ');
        },
        
        formatAmount(amount) {
            if (amount === null || amount === undefined) return '-';
            return new Intl.NumberFormat('en-NZ', {
                style: 'currency',
                currency: 'NZD'
            }).format(amount);
        },
        
        // Toast notifications
        showToast(message, type = 'info') {
            const id = ++this.toastId;
            this.toasts.push({ id, message, type });
        },
        
        removeToast(id) {
            this.toasts = this.toasts.filter(t => t.id !== id);
        }
    };
}
