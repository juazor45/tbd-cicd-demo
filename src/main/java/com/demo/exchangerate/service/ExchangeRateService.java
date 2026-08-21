package com.demo.exchangerate.service;

import com.demo.exchangerate.model.ExchangeRate;
import jakarta.enterprise.context.ApplicationScoped;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Fuente de datos en memoria: suficiente para la demo del pipeline,
 * sin base de datos ni servicios externos.
 */
@ApplicationScoped
public class ExchangeRateService {

    private static final Map<String, ExchangeRate> RATES = Map.of(
            "USD", new ExchangeRate("USD", "Dólar estadounidense",
                    new BigDecimal("3.750"), new BigDecimal("3.780"), LocalDate.now()),
            "EUR", new ExchangeRate("EUR", "Euro",
                    new BigDecimal("4.050"), new BigDecimal("4.120"), LocalDate.now()),
            "CLP", new ExchangeRate("CLP", "Peso chileno (x1000)",
                    new BigDecimal("3.950"), new BigDecimal("4.010"), LocalDate.now()),
            "GBP", new ExchangeRate("GBP", "Libra esterlina",
                    new BigDecimal("4.750"), new BigDecimal("4.820"), LocalDate.now())
    );

    public List<ExchangeRate> findAll() {
        return RATES.values().stream()
                .sorted(Comparator.comparing(ExchangeRate::currency))
                .toList();
    }

    public Optional<ExchangeRate> findByCurrency(String currency) {
        return Optional.ofNullable(RATES.get(currency.toUpperCase()));
    }

    public Optional<BigDecimal> convertToPen(String currency, BigDecimal amount) {
        return findByCurrency(currency)
                .map(rate -> amount.multiply(rate.buyRate()));
    }
}
