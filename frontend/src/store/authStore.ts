import { create } from 'zustand';

interface AuthState {
  isCustomerAuthenticated: boolean;
  customerId: string | null;
  isAdminAuthenticated: boolean;
  staffId: string | null;
  role: string | null;
  storeId: number | null;
  adminStoreResolved: boolean;
  setCustomerAuth: (id: string) => void;
  setAdminAuth: (id: string, role: string, storeId?: number | null) => void;
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
  setCustomerAuth: (id) => set({ isCustomerAuthenticated: true, customerId: id }),
  setAdminAuth: (id, role, storeId = null) => set({ isAdminAuthenticated: true, staffId: id, role, storeId, adminStoreResolved: true }),
  logoutCustomer: () => set({ isCustomerAuthenticated: false, customerId: null }),
  logoutAdmin: () => set({ isAdminAuthenticated: false, staffId: null, role: null, storeId: null, adminStoreResolved: false }),
}));
