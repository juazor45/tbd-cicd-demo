package com.demo.exchangerate;

import com.demo.exchangerate.model.ExchangeRate;
import com.demo.exchangerate.service.ExchangeRateService;
import jakarta.inject.Inject;
import jakarta.ws.rs.*;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

@Path("/api/v1/exchange-rates")
@Produces(MediaType.APPLICATION_JSON)
public class ExchangeRateResource {

    @Inject
    ExchangeRateService service;

    /** GET /api/v1/exchange-rates */
    @GET
    public List<ExchangeRate> getAll() {
        return service.findAll();
    }

    /** GET /api/v1/exchange-rates/USD */
    @GET
    @Path("/{currency}")
    public Response getByCurrency(@PathParam("currency") String currency) {
        return service.findByCurrency(currency)
                .map(rate -> Response.ok(rate).build())
                .orElse(Response.status(Response.Status.NOT_FOUND).build());
    }

    /** GET /api/v1/exchange-rates/USD/convert?amount=100 */
    @GET
    @Path("/{currency}/convert")
    public Response convert(@PathParam("currency") String currency,
                            @QueryParam("amount") BigDecimal amount) {
        if (amount == null) {
            return Response.status(Response.Status.BAD_REQUEST)
                    .entity(Map.of("error", "El parámetro 'amount' es obligatorio"))
                    .build();
        }
        return service.convertToPen(currency, amount)
                .map(result -> Response.ok(Map.of(
                        "currency", currency.toUpperCase(),
                        "amount", amount,
                        "amountInPen", result)).build())
                .orElse(Response.status(Response.Status.NOT_FOUND).build());
    }
}
