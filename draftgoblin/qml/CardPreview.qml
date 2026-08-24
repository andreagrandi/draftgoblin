import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root

    property var recommendation: null
    property bool loading: false
    property var imageState: null
    // Keep the compact preview's established sizing; only the wide build panel
    // opts into using the available panel height as an additional constraint.
    property bool constrainImageFrameToHeight: false

    readonly property bool imageCurrent: Boolean(
        recommendation
            && imageState
            && imageState.grp_id === recommendation.card.grp_id
    )
    readonly property bool imageLoading: Boolean(
        loading || imageCurrent && imageState.phase === "loading"
    )
    readonly property bool imageAvailable: Boolean(
        imageCurrent
            && imageState.image_path
            && imageState.phase === "ready"
    )

    color: Theme.surfaceLow
    border.color: Theme.outline
    border.width: 1
    radius: Theme.radius
    Accessible.role: Accessible.Pane
    Accessible.name: recommendation ? "Selected card, " + recommendation.card.name : "Card details"

    readonly property real imageFrameAvailableHeight: Math.max(
        0,
        root.height - Theme.panelPadding * 2
            - previewHeading.implicitHeight
            - (previewDetails.visible ? previewDetails.implicitHeight : 0)
            - previewLayout.spacing * (previewDetails.visible ? 3 : 2)
    )
    readonly property real imageFrameWidth: root.constrainImageFrameToHeight
        ? Math.max(
            0,
            Math.min(
                240,
                root.width - Theme.panelPadding * 2,
                root.imageFrameAvailableHeight / 1.4
            )
        )
        : Math.max(0, Math.min(240, root.width - Theme.panelPadding * 2))
    readonly property real imageFrameHeight: root.imageFrameWidth * 1.4

    ColumnLayout {
        id: previewLayout
        anchors.fill: parent
        anchors.margins: Theme.panelPadding
        spacing: 12

        Label {
            id: previewHeading
            objectName: "cardPreviewHeading"
            text: "SELECTED CARD"
            color: Theme.textMuted
            font.pixelSize: 10
            font.bold: true
            font.letterSpacing: 1.2
        }

        Rectangle {
            id: imageFrame
            objectName: "cardPreviewImageFrame"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: root.imageFrameWidth
            Layout.preferredHeight: root.imageFrameHeight
            color: "#171817"
            border.color: root.imageLoading ? Theme.warning : Theme.outline
            border.width: 2
            radius: 10
            clip: true

            Image {
                id: cardImage
                objectName: "cardPreviewImage"
                anchors.fill: parent
                source: root.imageAvailable ? root.imageState.image_path : ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                visible: root.imageAvailable && status === Image.Ready
            }

            ColumnLayout {
                id: previewFallback
                objectName: "cardPreviewFallback"
                anchors.centerIn: parent
                width: parent.width - 32
                spacing: 10
                visible: !cardImage.visible

                Rectangle {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                    radius: 24
                    color: root.recommendation && root.recommendation.card.colors.length > 0
                        ? Theme.colorForMana(root.recommendation.card.colors[0]) : Theme.surfaceHigh
                    opacity: 0.85
                }

                Label {
                    Layout.fillWidth: true
                    text: {
                        if (root.imageLoading)
                            return "Loading card image"
                        if (root.recommendation)
                            return root.recommendation.card.name
                        return "No card selected"
                    }
                    color: Theme.text
                    font.pixelSize: 16
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Label {
                    Layout.fillWidth: true
                    text: {
                        if (cardImage.status === Image.Error)
                            return "Card image could not be displayed."
                        if (root.imageCurrent)
                            return root.imageState.message
                        return "Card image unavailable"
                    }
                    color: Theme.textMuted
                    font.pixelSize: 11
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }
        }

        ColumnLayout {
            id: previewDetails
            objectName: "cardPreviewDetails"
            visible: root.recommendation !== null
            Layout.fillWidth: true
            spacing: 6

            Label {
                Layout.fillWidth: true
                text: root.recommendation ? root.recommendation.card.name : ""
                color: Theme.text
                font.pixelSize: 18
                font.bold: true
                wrapMode: Text.WordWrap
            }

            Label {
                Layout.fillWidth: true
                text: root.recommendation
                    ? root.recommendation.card.types.join(" · ")
                        + "   ·   MV " + root.recommendation.card.mana_value
                    : ""
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true

                Label {
                    text: root.recommendation ? "DG " + root.recommendation.score : ""
                    color: Theme.primary
                    font.family: fixedFontFamily
                    font.bold: true
                }

                Label {
                    text: root.recommendation && root.recommendation.win_rate !== null
                        ? "17L " + (root.recommendation.win_rate * 100).toFixed(1) + "%" : "17L —"
                    color: Theme.textMuted
                    font.family: fixedFontFamily
                }

                Label {
                    text: root.recommendation ? root.recommendation.letter_grade || "—" : ""
                    color: Theme.warning
                    font.bold: true
                }
            }

            Label {
                Layout.fillWidth: true
                text: root.recommendation ? root.recommendation.explanation || "" : ""
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }

        Item {
            Layout.fillHeight: true
        }
    }
}

