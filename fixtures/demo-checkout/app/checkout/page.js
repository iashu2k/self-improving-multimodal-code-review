import styles from "./checkout.module.css";

export default function CheckoutPage() {
  return (
    <main className={styles.container}>
      <h1 className={styles.heading}>Checkout</h1>
      <p className={styles.orderTotal}>Order total: $42.00</p>
      <button className={styles.placeOrder}>Place order</button>
    </main>
  );
}
