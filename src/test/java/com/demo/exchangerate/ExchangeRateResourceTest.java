package com.demo.exchangerate;

import io.quarkus.test.junit.QuarkusTest;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

@QuarkusTest
class ExchangeRateResourceTest {

    @Test
    void shouldReturnAllRates() {
        given()
          .when().get("/api/v1/exchange-rates")
          .then()
             .statusCode(200)
             .body("size()", is(3))
             .body("currency", hasItems("CLP", "EUR", "USD"));
    }

    @Test
    void shouldReturnRateByCurrency() {
        given()
          .when().get("/api/v1/exchange-rates/usd")
          .then()
             .statusCode(200)
             .body("currency", is("USD"))
             .body("buyRate", is(3.750f));
    }

    @Test
    void shouldReturn404ForUnknownCurrency() {
        given()
          .when().get("/api/v1/exchange-rates/XYZ")
          .then()
             .statusCode(404);
    }

    @Test
    void shouldConvertToPen() {
        given()
          .queryParam("amount", new BigDecimal("100"))
          .when().get("/api/v1/exchange-rates/USD/convert")
          .then()
             .statusCode(200)
             .body("amountInPen", is(375.000f));
    }

    @Test
    void shouldReturn400WhenAmountMissing() {
        given()
          .when().get("/api/v1/exchange-rates/USD/convert")
          .then()
             .statusCode(400);
    }

    @Test
    void healthShouldBeUp() {
        given()
          .when().get("/q/health/ready")
          .then()
             .statusCode(200)
             .body("status", is("UP"));
    }
}
