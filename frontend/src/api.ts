import axios from 'axios';
import { useAuthStore } from './store/authStore';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true, // for cookies
});

// We can add interceptors to handle 401s
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // For Admin session, or Customer session expiration
      useAuthStore.getState().logoutCustomer();
      useAuthStore.getState().logoutAdmin();
    }
    return Promise.reject(error);
  }
);

export default api;
