# Guard Order cancellation while a bank attempt is active

The existing Django admin list action **Отменить ожидающие заказы** can close a pending Order while its bank PaymentAttempt is still pending. It changes only FindMe Photo's Order status; it does not call the bank. A later bank success then opens payment-conflict attention rather than automatically granting the purchased originals.

This is outside the current operator action, which cancels one `NEW` T-Bank attempt without a hosted URL and leaves the Order pending. The new Order-detail confirmation and recovery runbook explicitly distinguish the two actions.

Bring this back into scope before exposing Order cancellation as a normal operator workflow for orders with active bank attempts, or before public paid-purchase activation. Decide whether to hide/reject the existing list action for those Orders or require a separate bank-state resolution first; verify late-success handling and operator messaging.
