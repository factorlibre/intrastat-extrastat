# Copyright 2022 FactorLibre - Luis J. Salvatierra <luis.salvatierra@factorlibre.com>


def pre_init_hook(cr):
    cr.execute(
        "ALTER TABLE account_invoice ADD COLUMN src_dest_country_id integer, "
        "ADD COLUMN intrastat_country boolean"
    )
