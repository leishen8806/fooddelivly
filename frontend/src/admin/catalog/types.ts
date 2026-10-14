export type Category = {
  id: number;
  name: Record<string, string>;
  sort_order: number;
  active: boolean;
  product_count?: number;
};

export type SaleWindow = { start: string; end: string; overnight?: boolean };

export type Product = {
  id: number;
  category_id: number;
  category_ids?: number[];
  name: Record<string, string>;
  description?: Record<string, string> | null;
  price_minor: number;
  currency: string;
  image_key?: string | null;
  available: boolean;
  sort_order: number;
  sweetness_enabled: boolean;
  sale_windows?: SaleWindow[];
  has_sale_window?: boolean;
  option_groups?: Array<{
    id: number;
    name: Record<string, string>;
    name_text?: string;
    kind: string;
    required: boolean;
    multi_select: boolean;
    max_select: number | null;
    active?: boolean;
    options: Array<{
      id: number;
      name: Record<string, string>;
      name_text?: string;
      price_delta_minor: number;
      is_default: boolean;
      active?: boolean;
    }>;
  }>;
};

export type StoreSettings = { currency: string };

export const localizedLabel = (value: Record<string, string> | undefined, language: string) =>
  value?.[language] || value?.en || value?.['zh-CN'] || value?.km || '';

export const hasMissingTranslation = (value: Record<string, string> | undefined) =>
  !value?.en?.trim() || !value?.['zh-CN']?.trim() || !value?.km?.trim();
