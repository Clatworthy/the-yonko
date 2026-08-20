package com.example.demo;

import java.util.List;
import org.junit.jupiter.api.Test;

public class CreditRepositoryTest {
    @Test
    void useCredits_countsNewIdentifiersOnly() {
        CreditRepository repo = new CreditRepository();
        CreditResponse response = repo.useCredits("c1", List.of("a", "b"));
        assert response.getRequested() == 2;
    }
}
