import { create } from 'zustand';

interface AuthState {
  isCustomerAuthenticated: boolean;
  customerId: string | null;
  isAdminAuthenticated: boolean;
  staffId: string | null;
  role: string | null;
  storeId: number | null;
  adminStoreResolved: boolean;
  catalogWriteAllowed: boolean;
  mustChangePassword: boolean;
  setCustomerAuth: (id: string) => void;
  setAdminAuth: (id: string, role: string, storeId?: number | null, catalogWriteAllowed?: boolean, mustChangePassword?: boolean) => void;
  logoutCustomer: () => void;
  logoutAdmin: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  isCustomerAuthenticated: false,
  customerId: null,
  isAdminAuthenticated: false,
  staffId: null,
  role: null,
  storeId: null,
  adminStoreResolved: false,
  catalogWriteAllowed: false,
  mustChangePassword: false,
  setCustomerAuth: (id) => set({ isCustomerAuthenticated: true, customerId: id }),
  setAdminAuth: (id, role, storeId = null, catalogWriteAllowed = false, mustChangePassword = false) => set({ isAdminAuthenticated: true, staffId: id, role, storeId, catalogWriteAllowed, mustChangePassword, adminStoreResolved: true }),
  logoutCustomer: () => set({ isCustomerAuthenticated: false, customerId: null }),
  logoutAdmin: () => set({ isAdminAuthenticated: false, staffId: null, role: null, storeId: null, catalogWriteAllowed: false, mustChangePassword: false, adminStoreResolved: false }),
}));
