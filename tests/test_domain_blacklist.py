import unittest

from moderation import _domain_matches_blacklist, _keyword_is_domain_token


class DomainBlacklistTokenTests(unittest.TestCase):
    def test_blocks_exact_suspicious_domain(self):
        self.assertTrue(_domain_matches_blacklist("pornhub.com"))

    def test_blocks_subdomain_of_suspicious(self):
        self.assertTrue(_domain_matches_blacklist("cdn.pornhub.com"))

    def test_keyword_as_token_blocks(self):
        # "casino" как отдельный токен в неизвестном домене
        self.assertTrue(_domain_matches_blacklist("best-casino.top"))

    def test_keyword_substring_does_not_falsely_block(self):
        # #7: раньше "sex" ловил essex, "bet" — betterhelp
        self.assertFalse(_domain_matches_blacklist("essex.ac.uk"))
        self.assertFalse(_domain_matches_blacklist("betterhelp.com"))
        self.assertFalse(_domain_matches_blacklist("scunthorpe.gov.uk"))

    def test_token_helper_boundaries(self):
        self.assertTrue(_keyword_is_domain_token("bet", "my-bet.net"))
        self.assertTrue(_keyword_is_domain_token("bet", "bet365.com"))
        self.assertFalse(_keyword_is_domain_token("bet", "betterhelp.com"))
        self.assertFalse(_keyword_is_domain_token("sex", "essex.com"))


if __name__ == "__main__":
    unittest.main()
