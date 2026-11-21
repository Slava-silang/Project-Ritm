import QtQuick

Flow {
    required property var appearance

    leftPadding: appearance.dp(appearance.graphite ? 20 : 12)
    rightPadding: leftPadding
    topPadding: appearance.dp(8)
    bottomPadding: appearance.dp(14)
    spacing: appearance.dp(8)
}
