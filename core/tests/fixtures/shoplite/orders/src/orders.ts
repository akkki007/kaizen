import { toCents } from "money-utils";
import { validatePayment } from "../../payments/src/payment/validation";

export class OrderService {
  total(items: number[]): number {
    return items.reduce((sum, i) => sum + toCents(i), 0);
  }

  create(items: number[], currency: string) {
    const total = this.total(items);
    return validatePayment({ amount: total, currency });
  }
}

export const createOrder = (items: number[]) => {
  return new OrderService().create(items, "USD");
};
