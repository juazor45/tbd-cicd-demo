package com.demo.exchangerate.model;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * Tipo de cambio de una divisa contra el Sol peruano (PEN).
 */
public record ExchangeRate(
        String currency,
        String description,
        BigDecimal buyRate,
        BigDecimal sellRate,
        LocalDate date) {
}
