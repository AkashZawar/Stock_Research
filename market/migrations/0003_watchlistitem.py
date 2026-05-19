from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0002_stocksearchlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="WatchlistItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("symbol", models.CharField(max_length=32, unique=True)),
                ("stock_name", models.CharField(blank=True, max_length=160)),
                ("buy_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("sell_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("check_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
    ]
