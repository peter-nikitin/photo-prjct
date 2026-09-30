# Cancel a T-Bank attempt without a payment link

Use this operator action when a pending Order has an active T-Bank PaymentAttempt with a bank `PaymentId` but no payment URL. It cancels the bank attempt, not the Order. It does not initiate another payment or return money from a completed payment.

1. In Django admin, open **Commerce → Orders** and search for the exact public Order number. The Order detail shows price, status, payment attempts, and evidence as read-only fields. Only the delivery email can be edited directly; other recovery operations are actions on the Order list.
2. On the Order detail, use **Отменить попытку в банке №…** for the exact pending attempt. This link appears only while the paid-purchase gate permits the operator, the operator has `commerce.change_order`, and the attempt has a bank `PaymentId` but no payment URL.
3. Read the confirmation page and submit once. The server records who requested cancellation, checks the bank's current status with `GetState`, calls `Cancel` only if the status is `NEW`, then checks `GetState` again. It records `CANCELED` through normal payment evidence only after the bank confirms it. The Order remains pending.
4. Return to the customer Order page. Once the attempt is canceled, **Повторить оплату** starts a new attempt for that same Order. The operator does not start it on the customer's behalf.

If the bank status changed, the bank call failed, or the final `CANCELED` check was inconclusive, the action reports that cancellation was not confirmed. The operator request remains in the admin audit, while the attempt stays pending without fabricated terminal evidence. Do not manually edit the attempt or Order status and do not start a second payment. Use the existing **Проверить статус в банке** action on the PaymentAttempt detail or wait for Commerce worker reconciliation, then inspect the Order again. An uncertain `Cancel` may already have reached the bank; checking its status is necessary before retrying.

The Order-list action **Отменить ожидающие заказы** closes an Order in FindMe Photo and does not cancel its bank PaymentAttempt. Do not use it as a substitute for this bank action.
