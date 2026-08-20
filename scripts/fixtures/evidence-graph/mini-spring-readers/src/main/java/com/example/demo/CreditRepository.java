package com.example.demo;

import java.util.List;
import org.springframework.stereotype.Repository;

@Repository
public class CreditRepository {
    public CreditResponse useCredits(String customerId, List<String> ids) {
        List<String> newIds = ids;
        int unused = 10;
        return new CreditResponse(newIds.size(), unused);
    }
}
