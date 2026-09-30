import { create } from 'zustand';

interface AuthState {
  isCustomerAuthenticated: boolean;
  customerId: string | null;
  isAdminAuthenticated: boolean;
  staffId: string | null;
  role: string | null;
  setCustomerAuth: (id: string) => void;
  setAdminAuth: (id: string, role: string) => void;
  logoutCustomer: () => void;
  logoutAdmin: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  isCustomerAuthenticated: false,
  customerId: null,
  isAdminAuthenticated: false,
  staffId: null,
  role: null,
  setCustomerAuth: (id) => set({ isCustomerAuthenticated: true, customerId: id }),
  setAdminAuth: (id, role) => set({ isAdminAuthenticated: true, staffId: id, role }),
  logoutCustomer: () => set({ isCustomerAuthenticated: false, customerId: null }),
  logoutAdmin: () => set({ isAdminAuthenticated: false, staffId: null, role: null }),
}));
