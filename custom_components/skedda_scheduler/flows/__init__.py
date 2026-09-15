"""Config and subentry flow helpers, kept out of the flow classes themselves.

Home Assistant's flow classes are awkward to unit test; the decisions worth
testing - normalisation, validation, what counts as the same account - live
here as plain functions instead.
"""
