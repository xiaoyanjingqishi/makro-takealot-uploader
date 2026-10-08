/**
 * 业务模块: users
 * 导出该领域的业务方法集
 */
export const usersMethods = {
  async loadUsers() {
    if (!this.currentUser || this.currentUser.role !== 'ADMIN') return;
    this.loadingUsers = true;
    try {
      const res = await fetch('/api/users', { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.userList = data.users || [];
      }
    } catch (e) {
      console.error('加载用户列表异常:', e);
    } finally {
      this.loadingUsers = false;
    }
  },


  openAddUserModal() {
    this.userModalTitle = '新建员工账号';
    this.userForm = {
      id: null,
      username: '',
      password: '',
      nickname: '',
      role: 'OPERATOR',
      store_ids: this.stores.map(s => s.id)
    };
    this.showUserModal = true;
  },


  openEditUserModal(u) {
    this.userModalTitle = `编辑用户: ${u.username}`;
    this.userForm = {
      id: u.id,
      username: u.username,
      password: '',
      nickname: u.nickname,
      role: u.role,
      store_ids: [...(u.store_ids || [])]
    };
    this.showUserModal = true;
  },


  async saveUser() {
    this.isSavingUser = true;
    try {
      if (!this.userForm.id) {
        const res = await fetch('/api/users', {
          method: 'POST',
          headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            username: this.userForm.username,
            password: this.userForm.password,
            nickname: this.userForm.nickname,
            role: this.userForm.role,
            store_ids: this.userForm.store_ids
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          this.showToast(`✅ 用户 ${this.userForm.username} 创建成功！`, 'success');
          this.showUserModal = false;
          await this.loadUsers();
        } else {
          alert('创建用户失败: ' + (data.detail || data.message || '未知错误'));
        }
      } else {
        const body = {
          nickname: this.userForm.nickname,
          role: this.userForm.role,
          store_ids: this.userForm.store_ids
        };
        if (this.userForm.password) {
          body.new_password = this.userForm.password;
        }
        const res = await fetch(`/api/users/${this.userForm.id}`, {
          method: 'PUT',
          headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(body)
        });
        const data = await res.json();
        if (res.ok && data.success) {
          this.showToast(`✅ 用户 ${this.userForm.username} 信息更新成功！`, 'success');
          this.showUserModal = false;
          await this.loadUsers();
        } else {
          alert('更新用户失败: ' + (data.detail || data.message || '未知错误'));
        }
      }
    } catch (e) {
      alert('保存用户网络异常: ' + e);
    } finally {
      this.isSavingUser = false;
    }
  },


  deleteUser(u) {
    this.openConfirm({
      title: `确定删除用户【${u.username}】？`,
      message: `删除后该账号将无法再登录系统。该用户名下选品将失去关联。`,
      type: 'danger',
      confirmText: '确认删除',
      onConfirm: async () => {
        try {
          const res = await fetch(`/api/users/${u.id}`, {
            method: 'DELETE',
            headers: this.getAuthHeaders()
          });
          const data = await res.json();
          if (res.ok && data.success) {
            this.showToast(`✅ 用户 ${u.username} 已删除`, 'success');
            await this.loadUsers();
          } else {
            alert('删除用户失败: ' + (data.detail || data.message || '未知错误'));
          }
        } catch (e) {
          alert('删除异常: ' + e);
        }
      }
    });
  },


  openAuthStoresModal(u) {
    this.targetAuthUser = u;
    this.targetAuthStoreIds = [...(u.store_ids || [])];
    this.showAuthStoresModal = true;
  },


  async saveUserStores() {
    if (!this.targetAuthUser) return;
    try {
      const res = await fetch(`/api/users/${this.targetAuthUser.id}/stores`, {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          store_ids: this.targetAuthStoreIds
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        this.showToast(`✅ 用户 ${this.targetAuthUser.username} 的店铺授权已保存！`, 'success');
        this.showAuthStoresModal = false;
        await this.loadUsers();
      } else {
        alert('保存店铺授权失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('保存店铺授权网络异常: ' + e);
    }
  },


  async loadOperators() {
    try {
      const res = await fetch('/api/users/operators', { headers: this.getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        this.operatorsList = data.operators || [];
      }
    } catch (e) {
      console.warn('获取员工列表异常:', e);
    }
  },


  openBatchAssignModal() {
    if (this.selectedIds.length === 0) {
      this.showToast('请先勾选需要分配归属的商品！', 'warning');
      return;
    }
    this.batchAssignTargetUserId = this.operatorsList.length > 0 ? this.operatorsList[0].id : '';
    this.showBatchAssignModal = true;
  },


  async submitBatchAssignUser() {
    if (this.selectedIds.length === 0 || this.isAssigningUser) return;
    this.isAssigningUser = true;
    try {
      const targetId = this.batchAssignTargetUserId ? parseInt(this.batchAssignTargetUserId, 10) : null;
      const res = await fetch('/api/products/batch-assign-user', {
        method: 'POST',
        headers: this.getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          product_ids: this.selectedIds,
          user_id: targetId
        })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`✅ ${data.message || '批量分配归属成功！'}`, 'success');
        this.showBatchAssignModal = false;
        this.selectedIds = [];
        await this.loadProducts(this.currentPage);
      } else {
        alert('批量分配失败: ' + (data.detail || data.message || '未知错误'));
      }
    } catch (e) {
      alert('批量分配网络异常: ' + e);
    } finally {
      this.isAssigningUser = false;
    }
  },
};

export default usersMethods;
