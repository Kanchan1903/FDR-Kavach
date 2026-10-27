from rest_framework import serializers

from .models import FDR, FDRCase, User


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class MfaSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\d{6}$")


class FDRInputSerializer(serializers.ModelSerializer):
    institution_id = serializers.UUIDField()
    renewal_of = serializers.SlugRelatedField(
        slug_field="public_id", queryset=FDR.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = FDR
        fields = (
            "institution_id",
            "renewal_of",
            "fdr_number",
            "bank_name",
            "branch_name",
            "principal_amount",
            "issue_date",
            "maturity_date",
            "purpose",
        )

    def validate(self, attrs):
        issue_date = attrs.get("issue_date", getattr(self.instance, "issue_date", None))
        maturity_date = attrs.get("maturity_date", getattr(self.instance, "maturity_date", None))
        if issue_date and maturity_date and maturity_date <= issue_date:
            raise serializers.ValidationError({"maturity_date": "Maturity date must be after issue date."})
        amount = attrs.get("principal_amount", getattr(self.instance, "principal_amount", None))
        if amount is not None and amount <= 0:
            raise serializers.ValidationError({"principal_amount": "Amount must be greater than zero."})
        return attrs


class FDRCaseUpdateSerializer(serializers.ModelSerializer):
    owner_id = serializers.SlugRelatedField(
        source="owner", slug_field="public_id", queryset=User.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = FDRCase
        fields = ("owner_id", "severity", "due_at", "status", "closure_evidence")

    def validate(self, attrs):
        if attrs.get("status") == FDRCase.Status.CLOSED:
            evidence = attrs.get("closure_evidence", self.instance.closure_evidence)
            if len(evidence.strip()) < 10:
                raise serializers.ValidationError(
                    {"closure_evidence": "Closure evidence must be at least 10 characters."}
                )
        return attrs
