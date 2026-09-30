export interface LocalizedText {
  en: string;
  'zh-CN'?: string;
  km?: string;
}

export interface Category {
  id: number;
  name: LocalizedText;
  sort_order: number;
  active: boolean;
}

export interface Product {
  id: number;
  category_id: number;
  name: LocalizedText;
  description?: LocalizedText;
  price_minor: number;
  currency: string;
  image_key?: string;
  available: boolean;
}

export interface OrderItem {
  product_id: number;
  quantity: number;
  options?: any;
}

export interface Order {
  id: number;
  public_code: string;
  room_number: string;
  order_status: string;
  payment_status: string;
  total_minor: number;
  currency: string;
}
