from decimal import Decimal

from django.db import models


class TradeReference(models.Model):
    STATUS_CHOICES = [
        ("watch", "Watch"),
        ("active", "Active"),
        ("closed", "Closed"),
    ]

    symbol = models.CharField(max_length=32)
    stock_name = models.CharField(max_length=160, blank=True)
    buy_price = models.DecimalField(max_digits=14, decimal_places=2)
    sell_price = models.DecimalField(max_digits=14, decimal_places=2)
    stop_loss = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="watch")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    @property
    def expected_return_percent(self):
        if not self.buy_price:
            return None
        return ((self.sell_price - self.buy_price) / self.buy_price) * Decimal("100")

    @property
    def risk_percent(self):
        if not self.buy_price or self.stop_loss is None:
            return None
        return ((self.buy_price - self.stop_loss) / self.buy_price) * Decimal("100")

    def as_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "stockName": self.stock_name,
            "buyPrice": float(self.buy_price),
            "sellPrice": float(self.sell_price),
            "stopLoss": float(self.stop_loss) if self.stop_loss is not None else None,
            "status": self.status,
            "note": self.note,
            "expectedReturnPercent": float(self.expected_return_percent) if self.expected_return_percent is not None else None,
            "riskPercent": float(self.risk_percent) if self.risk_percent is not None else None,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }
